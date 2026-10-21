import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, type Analysis, type Article, type Category, type HealthStatus, type Source, type SystemStatus } from './api';
import { updateNewsSearchParams } from './news-query';

const timeLabel = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : '采集时间';
const fullTime = (value?: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—';

function App() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [health, setHealth] = useState<HealthStatus>({ api: 'checking', database: 'checking' });
  const [catalogError, setCatalogError] = useState('');
  const healthRequest = useRef(0);
  const refreshHealth = useCallback(async () => {
    const requestId = ++healthRequest.current;
    setHealth({ api: 'checking', database: 'checking' });
    try {
      const nextHealth = await api.health();
      if (requestId === healthRequest.current) setHealth(nextHealth);
    } catch {
      if (requestId === healthRequest.current) setHealth({ api: 'unreachable', database: 'unknown' });
    }
  }, []);
  const refreshStatus = useCallback(async () => {
    try { setStatus(await api.status()); }
    catch { setStatus(null); }
  }, []);
  const refreshSystemStatus = useCallback(() => { void refreshStatus(); void refreshHealth(); }, [refreshHealth, refreshStatus]);
  useEffect(() => {
    Promise.all([api.categories(), api.sources(), api.status()])
      .then(([nextCategories, nextSources, nextStatus]) => { setCategories(nextCategories); setSources(nextSources); setStatus(nextStatus); })
      .catch((error: unknown) => setCatalogError(error instanceof Error ? error.message : '加载系统信息失败'));
    void refreshHealth();
  }, [refreshHealth]);
  return <div className="app-shell">
    <aside className="sidebar">
      <Link className="brand" to="/"><span className="brand-mark">T</span><span><b>TrendRadar</b><small>INTELLIGENCE CENTER</small></span></Link>
      <div className="side-label">工作台</div>
      <Link className="nav-link" to="/">◫ <span>新闻速览</span></Link>
      <Link className="nav-link" to="/status">◷ <span>采集状态</span></Link>
      <div className="side-label channel-label">频道</div>
      <Link className="channel-link" to="/?category=ai"><i className="dot dot-violet"/>AI</Link>
      <Link className="channel-link" to="/?category=technology"><i className="dot dot-blue"/>科技</Link>
      <Link className="channel-link" to="/?category=finance"><i className="dot dot-green"/>财经</Link>
      <Link className="channel-link" to="/?category=society"><i className="dot dot-orange"/>社会</Link>
      <div className="sidebar-footer"><span className="live-dot"/> 内网情报库 <span className="version">MVP</span></div>
    </aside>
    <main className="main-shell">
      <header className="topbar"><div className="crumb">个人情报系统 <span>/</span> <b>研究工作台</b></div><div className="top-state"><span className="live-dot"/> {status ? `${status.articleCount.toLocaleString()} 条情报` : '连接中'}</div></header>
      {catalogError && <div className="global-error">服务暂不可用：{catalogError}</div>}
      <Routes>
        <Route path="/" element={<NewsPage categories={categories} sources={sources} />} />
        <Route path="/news/:id" element={<DetailPage />} />
        <Route path="/status" element={<StatusPage status={status} health={health} onRefresh={refreshSystemStatus} />} />
        <Route path="*" element={<div className="empty-state">页面不存在 <Link to="/">返回新闻</Link></div>} />
      </Routes>
    </main>
  </div>;
}

function NewsPage({ categories, sources }: { categories: Category[]; sources: Source[] }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const [query, setQuery] = useState(searchParams.get('q') ?? '');
  const [page, setPage] = useState<{ data: Article[]; pagination: { limit: number; offset: number; total: number; hasMore: boolean } } | null>(null);
  const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  const category = searchParams.get('category') ?? '';
  const source = searchParams.get('source') ?? '';
  const from = searchParams.get('from') ?? '';
  const to = searchParams.get('to') ?? '';
  const offset = Number(searchParams.get('offset') ?? 0);
  const selectedCategory = categories.find((item) => item.id === category);
  const heading = selectedCategory?.name ?? '新闻速览';
  const params = useMemo(() => new URLSearchParams([...searchParams.entries()]), [searchParams]);

  useEffect(() => { setQuery(searchParams.get('q') ?? ''); }, [searchParams]);
  useEffect(() => {
    let alive = true; setLoading(true); setError('');
    api.list(params).then((result) => { if (alive) setPage(result); })
      .catch((reason: unknown) => { if (alive) setError(reason instanceof Error ? reason.message : '无法读取新闻'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [params]);

  const updateFilter = (key: string, value: string) => {
    setSearchParams(updateNewsSearchParams(searchParams, key, value));
  };
  const submitSearch = (event: React.FormEvent) => { event.preventDefault(); updateFilter('q', query.trim()); };
  const clearFilters = () => { setQuery(''); setSearchParams({}); };
  const active = Boolean(category || source || from || to || searchParams.get('q'));

  return <section className="content-area">
    <div className="page-heading"><div><div className="eyebrow">INTELLIGENCE FEED <span>·</span> 实时采集</div><h1>{heading}</h1><p>聚合关注领域的重要动态，AI 分析由后台采集流程生成。</p></div><div className="update-chip"><span className="live-dot"/> 采集入库</div></div>
    <div className="toolbar">
      <form className="search-box" onSubmit={submitSearch}><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、摘要关键词…"/><button type="submit">搜索</button></form>
      <select aria-label="来源筛选" value={source} onChange={(event) => updateFilter('source', event.target.value)}><option value="">全部来源</option>{sources.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
      <label className="date-filter"><span>从</span><input aria-label="起始日期" type="date" value={from.slice(0, 10)} onChange={(event) => updateFilter('from', event.target.value ? `${event.target.value}T00:00:00+08:00` : '')}/></label>
      <label className="date-filter"><span>至</span><input aria-label="结束日期" type="date" value={to.slice(0, 10)} onChange={(event) => updateFilter('to', event.target.value ? `${event.target.value}T23:59:59.999999+08:00` : '')}/></label>
      {active && <button className="clear-button" onClick={clearFilters}>清除筛选</button>}
    </div>
    <div className="feed-meta"><span>{page ? <>检索到 <b>{page.pagination.total.toLocaleString()}</b> 条新闻</> : '正在连接情报库'}</span><span>按发布时间排序</span></div>
    {loading && <div className="loading-state"><span className="spinner"/> 正在读取新闻…</div>}
    {error && <div className="error-panel"><b>新闻加载失败</b><span>{error}</span><button onClick={() => setSearchParams(new URLSearchParams(searchParams))}>重试</button></div>}
    {!loading && !error && page?.data.length === 0 && <div className="empty-state"><div className="empty-icon">⌕</div><b>没有找到匹配的新闻</b><span>试试更短的关键词，或清除部分筛选条件。</span>{active && <button onClick={clearFilters}>清除筛选</button>}</div>}
    <div className="news-list">{page?.data.map((article) => <NewsCard key={article.id} article={article} backTo={`${location.pathname}${location.search}`}/>)}</div>
    {page && page.pagination.total > page.pagination.limit && <div className="pagination"><button disabled={offset <= 0} onClick={() => updateFilter('offset', String(Math.max(0, offset - page.pagination.limit)))}>← 上一页</button><span>{Math.floor(offset / page.pagination.limit) + 1} / {Math.max(1, Math.ceil(page.pagination.total / page.pagination.limit))}</span><button disabled={!page.pagination.hasMore} onClick={() => updateFilter('offset', String(offset + page.pagination.limit))}>下一页 →</button></div>}
  </section>;
}

function NewsCard({ article, backTo }: { article: Article; backTo: string }) {
  return <Link className="news-card" to={`/news/${article.id}`} state={{ backTo }}>
    <div className="card-main"><div className="card-kicker"><span className="source-name">{article.source.name}</span><span className="separator">·</span><time>{timeLabel(article.publishedAt ?? article.firstSeenAt)}</time>{article.categories.map((item) => <span key={item.id} className="tag">{item.name}</span>)}</div>
      <h2>{article.title}</h2>{article.summary && <p>{article.summary}</p>}
      {article.analysisStatus === 'completed' && <div className="analysis-teaser"><span>AI 摘要</span>{article.analysisSummary ?? '分析已完成，打开详情查看结构化分析。'}</div>}
    </div><div className={`analysis-badge status-${article.analysisStatus}`}><span/>{statusLabel(article.analysisStatus)}</div>
  </Link>;
}

function statusLabel(status: Article['analysisStatus']) {
  return ({ completed: '已分析', failed: '分析失败', processing: '分析中', pending: '待分析', not_requested: '未启用' })[status];
}

function DetailPage() {
  const { id = '' } = useParams(); const navigate = useNavigate(); const location = useLocation();
  const backTo = (location.state as { backTo?: unknown } | null)?.backTo;
  const returnToList = () => navigate(typeof backTo === 'string' && backTo.startsWith('/') && !backTo.startsWith('//') ? backTo : '/');
  const [article, setArticle] = useState<Article | null>(null); const [analysis, setAnalysis] = useState<Analysis | null>(null); const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    Promise.all([api.article(id), api.analysis(id)]).then(([nextArticle, nextAnalysis]) => {
      if (alive) { setArticle(nextArticle); setAnalysis(nextAnalysis); }
    }).catch((reason: unknown) => { if (alive) setError(reason instanceof Error ? reason.message : '无法读取新闻详情'); });
    return () => { alive = false; };
  }, [id]);
  if (error) return <section className="content-area"><button className="back-link" onClick={returnToList}>← 返回</button><div className="error-panel"><b>详情加载失败</b><span>{error}</span></div></section>;
  if (!article || !analysis) return <div className="loading-state"><span className="spinner"/> 正在加载详情…</div>;
  return <section className="content-area detail-page"><button className="back-link" onClick={returnToList}>← 返回列表</button>
    <div className="detail-header"><div className="card-kicker"><span className="source-name">{article.source.name}</span><span className="separator">·</span><time>{fullTime(article.publishedAt ?? article.firstSeenAt)}</time>{article.categories.map((item) => <span key={item.id} className="tag">{item.name}</span>)}</div><h1>{article.title}</h1>{article.url && <a className="original-link" href={article.url} target="_blank" rel="noreferrer">查看原文 ↗</a>}</div>
    <div className="detail-grid"><article className="analysis-panel"><div className="section-title"><span className="sparkle">✳</span><div><b>AI 情报分析</b><small>采集后后台生成 · 非实时推理</small></div><span className={`analysis-badge status-${analysis.status}`}><span/>{statusLabel(analysis.status)}</span></div>
      {analysis.status === 'completed' ? <>
        {analysis.evidenceScope && <div className="evidence-note">分析依据：{({ title: '仅新闻标题', summary: '新闻标题与摘要', content: '新闻正文' })[analysis.evidenceScope]}</div>}
        {analysis.summary && <section className="analysis-section"><h3>摘要</h3><p className="summary-text">{analysis.summary}</p></section>}
        <AnalysisList title="关键事实" items={analysis.keyFacts}/><section className="analysis-section"><h3>事件背景</h3><p>{analysis.background || '暂无背景信息。'}</p></section>
        <AnalysisList title="可能影响" items={analysis.potentialImpacts}/><AnalysisList title="尚未确认" items={analysis.uncertainties}/>
        <footer className="analysis-foot">{analysis.modelName} · Prompt {analysis.promptVersion} · 分析于 {fullTime(analysis.analyzedAt)}</footer>
      </> : <div className="analysis-placeholder"><div className="placeholder-mark">{analysis.status === 'failed' ? '!' : '◷'}</div><b>{analysis.status === 'failed' ? '本次分析未成功' : analysis.status === 'not_requested' ? '后台分析当前未启用' : '分析尚未完成'}</b><p>{analysis.status === 'failed' ? '原始新闻已保存，后台任务可以重试。' : '此页面只展示采集后生成的分析，不会在打开页面时临时调用模型。'}</p>{analysis.status === 'failed' && analysis.errorCode && <code className="analysis-error-code">错误代码：{analysis.errorCode}</code>}</div>}
    </article><aside className="source-panel"><div className="side-label">新闻信息</div><dl><dt>来源</dt><dd>{article.source.name}</dd><dt>发布时间</dt><dd>{fullTime(article.publishedAt)}</dd><dt>首次采集</dt><dd>{fullTime(article.firstSeenAt)}</dd><dt>内容依据</dt><dd>{article.contentStatus === 'title_only' ? '仅标题' : article.contentStatus === 'summary_available' ? '标题与摘要' : '新闻正文'}</dd><dt>分析状态</dt><dd>{statusLabel(article.analysisStatus)}</dd></dl>{article.url && <a className="source-open" href={article.url} target="_blank" rel="noreferrer">打开原始来源 ↗</a>}</aside></div>
  </section>;
}

function AnalysisList({ title, items = [] }: { title: string; items?: string[] }) {
  if (!items.length) return null;
  return <section className="analysis-section"><h3>{title}</h3><ul>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul></section>;
}

function StatusPage({ status, health, onRefresh }: { status: SystemStatus | null; health: HealthStatus; onRefresh: () => void }) {
  useEffect(() => { onRefresh(); }, [onRefresh]);
  const apiHealthLabel = health.api === 'reachable' ? 'API 可达' : health.api === 'unreachable' ? 'API 不可达' : '检查中';
  const databaseHealthLabel = health.database === 'ok' ? 'PostgreSQL 正常' : health.database === 'unavailable' ? 'PostgreSQL 不可用' : health.database === 'unknown' ? '状态未知' : '检查中';
  return <section className="content-area status-page"><div className="page-heading"><div><div className="eyebrow">SYSTEM MONITOR</div><h1>采集状态</h1><p>查看最近一次采集、AI 分析与数据库状态。</p></div><button className="refresh-button" onClick={onRefresh}>↻ 刷新</button></div>
    <div className="status-cards"><HealthCard label="Backend API" value={apiHealthLabel} healthy={health.api === 'reachable'} pending={health.api === 'checking'} detail="由 /api/v1/health 实时检查可达性"/><HealthCard label="PostgreSQL" value={databaseHealthLabel} healthy={health.database === 'ok'} pending={health.database === 'checking' || health.database === 'unknown'} detail="数据库健康状态与 API 可达性分开显示"/>{status ? <><div className="metric-card"><span>新闻总量</span><b>{status.articleCount.toLocaleString()} <small>条</small></b><small>最新更新 {fullTime(status.latestArticleAt)}</small></div><div className="metric-card"><span>最近采集</span><b className="run-status">{status.latestRun ? runLabel(status.latestRun.status) : '尚无记录'}</b><small>{fullTime(status.latestRun?.startedAt)}</small></div></> : <div className="metric-card"><span>采集状态</span><b className="health-pending">暂不可用</b><small>任务状态请求失败</small></div>}</div>
    {!status ? <div className="error-panel"><b>系统状态不可用</b><span>无法读取采集任务状态；请结合上方 API 与数据库健康卡检查。</span></div> : status.latestRun ? <div className="run-card"><div className="run-card-head"><div><div className="eyebrow">LATEST INGESTION RUN</div><h2>最近任务详情</h2></div><span className={`run-pill run-${status.latestRun.status}`}>{runLabel(status.latestRun.status)}</span></div><div className="run-stats"><div><b>{status.latestRun.articleCount}</b><span>处理新闻</span></div><div><b>{status.latestRun.insertedCount}</b><span>新入库</span></div><div><b>{status.latestRun.updatedCount}</b><span>更新去重</span></div><div><b>{status.latestRun.analysisSucceeded}</b><span>AI 成功</span></div><div><b>{status.latestRun.analysisFailed}</b><span>AI 失败</span></div></div>{status.latestRun.errorSummary && <div className="run-warning">{status.latestRun.errorSummary}</div>}</div> : <div className="empty-state"><b>还没有采集运行记录</b><span>配置并运行 TrendRadar 采集适配后，这里会显示任务状态。</span></div>}
  </section>;
}

function HealthCard({ label, value, healthy, pending, detail }: { label: string; value: string; healthy: boolean; pending: boolean; detail: string }) {
  return <div className="metric-card"><span>{label}</span><b className={healthy ? 'healthy' : pending ? 'health-pending' : 'health-unavailable'}>{healthy && <i/>}{value}</b><small>{detail}</small></div>;
}

function runLabel(status: string) { return ({ completed: '已完成', partial: '部分完成', failed: '失败', running: '运行中' })[status] ?? status; }

export default App;
