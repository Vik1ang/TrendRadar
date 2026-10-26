import SwiftUI

enum NewsDateLocale {
  static func current(environmentLocale: Locale) -> Locale {
    #if DEBUG
      let arguments = ProcessInfo.processInfo.arguments
      if arguments.contains("--enable-test-locale-injection"),
        let index = arguments.firstIndex(of: "--test-locale"),
        arguments.indices.contains(index + 1)
      {
        return Locale(identifier: arguments[index + 1])
      }
    #endif
    return environmentLocale
  }
}

struct ArticleRow: View {
  @Environment(\.locale) private var locale
  let article: NewsArticle
  private var dateLocale: Locale { NewsDateLocale.current(environmentLocale: locale) }

  private func dateText(_ date: Date) -> String {
    date.formatted(.dateTime.month(.abbreviated).day().hour().minute().locale(dateLocale))
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text(article.title).font(.headline).foregroundStyle(.primary)
        .accessibilityIdentifier("article-title-\(article.id)").fixedSize(
          horizontal: false, vertical: true)
      if let summary = article.summary {
        Text(summary).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
      }
      HStack(spacing: 8) {
        Text(article.source.name)
        Spacer(minLength: 0)
        if article.categories.isEmpty {
          Text("未分类").font(.caption2).padding(.horizontal, 7).padding(.vertical, 4)
            .background(.quaternary, in: Capsule())
            .accessibilityIdentifier("uncategorized-tag")
        }
        ForEach(article.categories.prefix(2)) { category in
          Text(category.name).font(.caption2).padding(.horizontal, 7).padding(.vertical, 4)
            .background(.quaternary, in: Capsule())
        }
      }.font(.caption).foregroundStyle(.secondary)
      HStack(spacing: 10) {
        if let publishedAt = article.publishedAt {
          Text(verbatim: "发布 \(dateText(publishedAt))")
            .accessibilityIdentifier("published-time-\(article.id)")
        } else {
          Text("未提供发布时间")
            .accessibilityIdentifier("published-time-\(article.id)")
        }
        Text(verbatim: "采集 \(dateText(article.firstSeenAt))")
      }.font(.caption2).foregroundStyle(.tertiary)
    }
    .padding(.vertical, 6)
    .contentShape(Rectangle())
  }
}

struct LoadStateView: View {
  let title: String
  let message: String
  let action: (() -> Void)?
  var body: some View {
    ContentUnavailableView {
      Label(title, systemImage: "exclamationmark.triangle")
    } description: {
      Text(message)
    } actions: {
      if let action {
        Button("重试", action: action).buttonStyle(.bordered)
          .accessibilityIdentifier("retry-button")
      }
    }
  }
}

struct LoadingRow: View {
  var body: some View {
    HStack {
      Spacer()
      ProgressView()
      Spacer()
    }.padding()
  }
}
