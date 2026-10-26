import SwiftUI

@MainActor
@Observable
final class NewsFeedModel {
  var categories: [NewsCategory] = []
  var selectedCategory: String?
  var articles: [NewsArticle] = []
  var isLoading = false
  var isRefreshing = false
  var error: String?
  var hasMore = false
  private var offset = 0
  private let pageSize = 20
  let api: any TrendRadarAPI

  init(api: any TrendRadarAPI) { self.api = api }

  func load() async {
    guard !isLoading, !isRefreshing else { return }
    guard categories.isEmpty else {
      await refresh()
      return
    }
    isLoading = true
    error = nil
    do {
      categories = try await api.categories().sorted {
        ($0.sortOrder ?? .max) < ($1.sortOrder ?? .max)
      }
      selectedCategory = nil
      try await fetch(reset: true)
    } catch { self.error = error.localizedDescription }
    isLoading = false
  }

  func select(_ category: String?) async {
    guard selectedCategory != category, !isLoading, !isRefreshing else { return }
    selectedCategory = category
    isLoading = true
    error = nil
    do { try await fetch(reset: true) } catch { self.error = error.localizedDescription }
    isLoading = false
  }

  func refresh() async {
    guard !isLoading, !isRefreshing else { return }
    isRefreshing = true
    error = nil
    do { try await fetch(reset: true) } catch { self.error = error.localizedDescription }
    isRefreshing = false
  }

  func loadMore() async {
    guard hasMore, !isLoading, !isRefreshing, error == nil else { return }
    isLoading = true
    do { try await fetch(reset: false) } catch { self.error = error.localizedDescription }
    isLoading = false
  }

  private func fetch(reset: Bool) async throws {
    let nextOffset = reset ? 0 : offset
    let page = try await api.news(
      NewsQuery(category: selectedCategory, limit: pageSize, offset: nextOffset))
    articles = reset ? page.data : articles + page.data
    offset = page.pagination.offset + page.data.count
    hasMore = page.pagination.hasMore
  }
}

struct NewsFeedView: View {
  @Environment(\.scenePhase) private var scenePhase
  @State private var model: NewsFeedModel
  @State private var selectedArticle: NewsArticle?
  @State private var enteredBackground = false
  @Binding private var deviceAuthPresentation: DeviceAuthPresentation?

  init(
    api: any TrendRadarAPI,
    deviceAuthPresentation: Binding<DeviceAuthPresentation?> = .constant(nil)
  ) {
    _model = State(initialValue: NewsFeedModel(api: api))
    _deviceAuthPresentation = deviceAuthPresentation
  }

  var body: some View {
    NavigationStack {
      Group {
        if model.isLoading && model.articles.isEmpty {
          ProgressView("正在加载新闻…")
        } else if let error = model.error, model.articles.isEmpty {
          LoadStateView(title: "加载失败", message: error) { Task { await model.load() } }
        } else if model.articles.isEmpty {
          ContentUnavailableView("暂无新闻", systemImage: "newspaper", description: Text("当前频道还没有新闻。"))
        } else {
          List {
            ForEach(model.articles) { article in
              Button {
                selectedArticle = article
              } label: {
                ArticleRow(article: article)
              }
              .buttonStyle(.plain)
              .accessibilityIdentifier("article-\(article.id)")
              .task { if article.id == model.articles.last?.id { await model.loadMore() } }
            }
            if model.isLoading { LoadingRow().listRowSeparator(.hidden) }
          }
          .listStyle(.plain)
          .refreshable { await model.refresh() }
        }
      }
      .navigationTitle("TrendRadar")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button {
            deviceAuthPresentation = .manual
          } label: {
            Label("设备认证", systemImage: "person.badge.key")
          }
          .accessibilityIdentifier("device-auth-button")
        }
        ToolbarItem(placement: .topBarTrailing) {
          Image(systemName: "line.3.horizontal.decrease.circle").foregroundStyle(.secondary)
        }
      }
      .safeAreaInset(edge: .top, spacing: 0) { categoryPicker }
      .navigationDestination(item: $selectedArticle) { article in
        ArticleDetailView(articleID: article.id, api: model.api)
      }
      .task { await model.load() }
      .onChange(of: scenePhase) { _, phase in
        if phase == .background {
          enteredBackground = true
        } else if phase == .active, enteredBackground {
          enteredBackground = false
          Task { await model.load() }
        }
      }
      .onReceive(
        NotificationCenter.default.publisher(for: .deviceAuthenticationRestored)
          .receive(on: DispatchQueue.main)
      ) { _ in Task { await model.load() } }
    }
  }

  private func channelLabel(_ title: String, selected: Bool) -> some View {
    Text(title).font(.subheadline.weight(selected ? .semibold : .regular))
      .padding(.horizontal, 14).padding(.vertical, 8)
      .foregroundStyle(selected ? Color(.systemBackground) : .primary)
      .background(selected ? Color.primary : Color.clear, in: Capsule())
      .overlay(Capsule().strokeBorder(selected ? .clear : Color.secondary.opacity(0.25)))
  }

  private var categoryPicker: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        Button {
          Task { await model.select(nil) }
        } label: {
          channelLabel("全部", selected: model.selectedCategory == nil)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("channel-all")
        ForEach(model.categories) { category in
          Button {
            Task { await model.select(category.id) }
          } label: {
            channelLabel(category.name, selected: model.selectedCategory == category.id)

          }.buttonStyle(.plain)
            .accessibilityIdentifier("channel-\(category.id)")
        }
      }.padding(.horizontal).padding(.vertical, 8)
    }
    .background(.background)
  }
}
