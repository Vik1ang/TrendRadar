import Foundation

struct NewsSource: Codable, Hashable, Identifiable {
  let id: String
  let name: String
  let type: String
}

struct NewsCategory: Codable, Hashable, Identifiable {
  let id: String
  let name: String
  let sortOrder: Int?
}

struct NewsArticle: Codable, Hashable, Identifiable {
  let id: String
  let title: String
  let source: NewsSource
  let publishedAt: Date?
  let firstSeenAt: Date
  let url: URL?
  let summary: String?
  let analysisSummary: String?
  let contentStatus: String
  let analysisStatus: AnalysisStatus
  let categories: [ArticleCategory]

  var displayDate: Date { publishedAt ?? firstSeenAt }
}

struct ArticleCategory: Codable, Hashable, Identifiable {
  let id: String
  let name: String
}

struct Pagination: Codable, Hashable {
  let limit: Int
  let offset: Int
  let total: Int
  let hasMore: Bool
}

struct NewsPage: Codable, Hashable {
  let data: [NewsArticle]
  let pagination: Pagination
}

enum AnalysisStatus: String, Codable, Hashable {
  case pending, processing, completed, failed
  case notRequested = "not_requested"
}

struct ArticleAnalysis: Codable, Hashable {
  let status: AnalysisStatus
  let errorCode: String?
  let summary: String?
  let keyFacts: [String]?
  let background: String?
  let potentialImpacts: [String]?
  let uncertainties: [String]?
  let evidenceScope: String?
  let modelName: String?
  let promptVersion: String?
  let analysisVersion: Int?
  let analyzedAt: Date?
}

struct APIErrorBody: Decodable {
  struct Detail: Decodable {
    let code: String
    let message: String
  }
  let error: Detail
  let requestId: String?
}

struct FastifyRouteError: Decodable {
  let message: String
  let error: String?
  let statusCode: Int?
}
