import SwiftUI

@MainActor
@Observable
final class SearchModel {
  var text = ""
  var articles: [NewsArticle] = []
  var isLoading = false
  var error: String?
  var hasMore = false
  private var offset = 0
  private var generation = 0
  private let pageSize = 20
  let api: any TrendRadarAPI

  init(api: any TrendRadarAPI) { self.api = api }

  func search(reset: Bool = true) async {
    if reset {
      generation += 1
      offset = 0
      articles = []
      error = nil
      hasMore = false
    }
    let requestGeneration = generation
    let queryText = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !queryText.isEmpty else {
      isLoading = false
      return
    }
    isLoading = true
    defer { if requestGeneration == generation { isLoading = false } }
    do {
      try await Task.sleep(for: .milliseconds(350))
      try Task.checkCancellation()
      let page = try await api.search(
        NewsQuery(text: queryText, limit: pageSize, offset: offset))
      try Task.checkCancellation()
      guard requestGeneration == generation,
        text.trimmingCharacters(in: .whitespacesAndNewlines) == queryText
      else { return }
      articles = reset ? page.data : articles + page.data
      offset = page.pagination.offset + page.data.count
      hasMore = page.pagination.hasMore
    } catch is CancellationError {
    } catch {
      if requestGeneration == generation { self.error = error.localizedDescription }
    }
  }

  func loadMore() async {
    guard hasMore, !isLoading, error == nil else { return }
    await search(reset: false)
  }
}

struct SearchView: View {
  @State private var model: SearchModel
  @State private var selectedArticle: NewsArticle?
  init(api: any TrendRadarAPI) { _model = State(initialValue: SearchModel(api: api)) }

  var body: some View {
    NavigationStack {
      Group {
        if model.isLoading && model.articles.isEmpty {
          ProgressView("正在搜索…")
        } else if let error = model.error {
          LoadStateView(title: "搜索失败", message: error) { Task { await model.search() } }
        } else if !model.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
          && model.articles.isEmpty && !model.isLoading
        {
          ContentUnavailableView.search(text: model.text)
            .accessibilityIdentifier("search-empty-state")
        } else if model.text.isEmpty {
          ContentUnavailableView(
            "搜索新闻", systemImage: "magnifyingglass", description: Text("输入关键词搜索标题、摘要与分析内容。"))
        } else {
          List {
            ForEach(model.articles) { article in
              Button {
                selectedArticle = article
              } label: {
                ArticleRow(article: article)
              }.buttonStyle(.plain)
                .accessibilityIdentifier("article-\(article.id)")
                .task { if article.id == model.articles.last?.id { await model.loadMore() } }
            }
            if model.isLoading { LoadingRow().listRowSeparator(.hidden) }
          }.listStyle(.plain)
        }
      }
      .navigationTitle("搜索")
      .searchable(text: $model.text, prompt: "搜索新闻")
      .task(id: model.text) { await model.search() }
      .navigationDestination(item: $selectedArticle) { article in
        ArticleDetailView(articleID: article.id, api: model.api)
      }
    }
  }
}
