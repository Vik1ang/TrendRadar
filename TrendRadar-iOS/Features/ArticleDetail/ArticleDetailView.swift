import SwiftUI

@MainActor
@Observable
final class ArticleDetailModel {
  var article: NewsArticle?
  var analysis: ArticleAnalysis?
  var isLoading = true
  var error: String?
  private let id: String
  let api: any TrendRadarAPI

  init(id: String, api: any TrendRadarAPI) {
    self.id = id
    self.api = api
  }

  func load() async {
    isLoading = true
    error = nil
    do {
      async let articleRequest = api.article(id: id)
      async let analysisRequest = api.analysis(id: id)
      article = try await articleRequest
      analysis = try await analysisRequest
    } catch { self.error = error.localizedDescription }
    isLoading = false
  }
}

struct ArticleDetailView: View {
  @State private var model: ArticleDetailModel
  init(articleID: String, api: any TrendRadarAPI) {
    _model = State(initialValue: ArticleDetailModel(id: articleID, api: api))
  }

  var body: some View {
    Group {
      if model.isLoading {
        ProgressView("正在加载文章…")
      } else if let error = model.error, model.article == nil {
        LoadStateView(title: "文章加载失败", message: error) { Task { await model.load() } }
      } else if let article = model.article {
        ScrollView {
          VStack(alignment: .leading, spacing: 20) {
            Text(article.title).font(.largeTitle.bold())
              .accessibilityIdentifier("detail-title").fixedSize(
                horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 6) {
              Text(article.source.name).font(.subheadline.weight(.medium))
                .accessibilityIdentifier("detail-source")
              if let publishedAt = article.publishedAt {
                Text("发布时间：\(publishedAt, format: .dateTime.year().month().day().hour().minute())")
              } else {
                Text("未提供发布时间").accessibilityIdentifier("detail-publication-unavailable")
              }
              Text(
                "采集时间：\(article.firstSeenAt, format: .dateTime.year().month().day().hour().minute())"
              )
            }.font(.caption).foregroundStyle(.secondary)
            if let summary = article.summary {
              Text(summary).font(.title3).fontWeight(.medium)
                .accessibilityIdentifier("detail-summary")
            } else {
              Text("暂无摘要").font(.subheadline).foregroundStyle(.secondary)
                .accessibilityIdentifier("detail-summary-unavailable")
            }
            if let url = article.url {
              Link(destination: url) { Label("阅读原文", systemImage: "arrow.up.right.square") }
                .font(.subheadline.weight(.semibold))
            }
            Divider()
            analysisSection(article: article)
          }.frame(maxWidth: .infinity, alignment: .leading).padding(20)
        }
      }
    }
    .navigationTitle("新闻详情")
    .navigationBarTitleDisplayMode(.inline)
    .task { await model.load() }
  }

  @ViewBuilder private func analysisSection(article: NewsArticle) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      Text("AI 分析").font(.title2.bold())
      if let error = model.error {
        Text("分析暂不可用：\(error)").foregroundStyle(.secondary)
        Button("重试") { Task { await model.load() } }
          .buttonStyle(.bordered).accessibilityIdentifier("retry-button")
      } else if let analysis = model.analysis {
        switch analysis.status {
        case .notRequested:
          Text("尚未请求分析。打开文章不会触发 AI 分析。")
            .accessibilityIdentifier("analysis-not-requested")
        case .pending: Label("分析排队中", systemImage: "clock").foregroundStyle(.secondary)
        case .processing: Label("分析处理中", systemImage: "hourglass").foregroundStyle(.secondary)
        case .failed:
          Label("分析失败：\(analysis.errorCode ?? "暂时无法获取")", systemImage: "exclamationmark.circle")
            .foregroundStyle(.secondary)
        case .completed:
          if let summary = analysis.summary ?? article.analysisSummary {
            Text(summary).font(.body).accessibilityIdentifier("analysis-summary")
          }
          if let facts = analysis.keyFacts, !facts.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
              Text("要点").font(.headline)
              ForEach(Array(facts.enumerated()), id: \.offset) { _, fact in
                Label(fact, systemImage: "circle.fill").font(.subheadline)
              }
            }
          }
        }
      }
    }
  }
}
