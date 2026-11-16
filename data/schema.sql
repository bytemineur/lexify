-- ============================================================
--  Lexify 统一数据库（SQLite）
--  ------------------------------------------------------------
--  三个看板共用这一个库，替代原来分散的 tender.db / bounty.db。
--  build_data.py 只读这里的 tenders / sources / hearings / notices，
--  导出成 web/data/*.js —— 也就是「数据库是唯一事实源」。
--
--  约定：
--   · 时间一律存文本，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM（本地时间）
--   · 金额一律存「元」，REAL；缺失用 NULL，绝不用 0 顶替
--   · 每条记录都要能追溯：source + source_id（或 hash_key）+ fetched_at
--   · 采集/导入的每一次动作都写一条 ingest_runs，用于对账
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── 招标公告 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    hash_key            TEXT NOT NULL,          -- 去重键：标题+采购人+发布日 的 sha1
    source_id           TEXT NOT NULL,          -- 对应 sources.id
    source_url          TEXT,
    title               TEXT NOT NULL,
    notice_type         TEXT,                   -- 公开招标/竞争性磋商/中标公告…
    is_result           INTEGER DEFAULT 0,      -- 是否结果公告
    purchaser           TEXT,                   -- 采购人
    agency              TEXT,                   -- 代理机构
    budget_amount       REAL,                   -- 预算金额（元）
    province            TEXT,
    city                TEXT,
    industry            TEXT,                   -- 行业分类：法律服务/服务/信息技术…
    publish_time        TEXT,
    deadline            TEXT,                   -- 投标截止
    deadline_raw        TEXT,                   -- 原文里的截止表述
    deadline_confident  INTEGER DEFAULT 0,      -- 截止时间是否可信（0=需核对）
    content_text        TEXT,
    is_primary          INTEGER DEFAULT 1,      -- 同一标的的主公告=1，转载/关联=0
    cluster_id          INTEGER,                -- 关联到主公告的 id
    -- 下面几列由招标采集器直接写，保留是为了让它能直连这个库（不丢字段）
    currency            TEXT DEFAULT 'CNY',
    raw_path            TEXT,                   -- 原始页面在抓取缓存里的相对路径
    attachments         TEXT,                   -- JSON：附件列表
    extract_meta        TEXT,                   -- JSON：抽取过程的元信息
    extra               TEXT,                   -- JSON：站点特有字段
    created_at          TEXT,
    fetched_at          TEXT,
    updated_at          TEXT,
    UNIQUE(hash_key)
);
CREATE INDEX IF NOT EXISTS idx_tenders_pub      ON tenders(publish_time);
CREATE INDEX IF NOT EXISTS idx_tenders_deadline ON tenders(deadline);
CREATE INDEX IF NOT EXISTS idx_tenders_prov     ON tenders(province);
CREATE INDEX IF NOT EXISTS idx_tenders_source   ON tenders(source_id);
CREATE INDEX IF NOT EXISTS idx_tenders_amount   ON tenders(budget_amount);

-- ── 数据源（34 个采购平台 / 法院站点）────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,           -- 站点标识，如 ggzy / rmfygg_kt
    name            TEXT NOT NULL,
    base_url        TEXT,
    enabled         INTEGER DEFAULT 1,
    mode            TEXT,                       -- http / browser
    rule_path       TEXT,                       -- 解析规则文件
    rule_version    TEXT,
    schedule        TEXT,
    health          TEXT DEFAULT 'idle',        -- idle / ok / fail
    fail_count      INTEGER DEFAULT 0,
    last_run_at     TEXT,
    last_success_at TEXT,
    last_error      TEXT,
    runs            INTEGER DEFAULT 0           -- 累计成功采集次数（采集器维护）
);

-- ── 开庭公告 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hearings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT,
    court           TEXT,
    province        TEXT,
    city            TEXT,
    case_no         TEXT,
    case_kind       TEXT,                       -- 民初/民终/刑初…
    cause           TEXT,                       -- 案由
    cause_group     TEXT,                       -- 建筑工程/票据/商标知产…
    is_commercial   INTEGER DEFAULT 0,
    plaintiff       TEXT,
    defendant       TEXT,
    defendant_type  TEXT,                       -- company / mixed / person / unknown
    company_count   INTEGER DEFAULT 0,
    person_count    INTEGER DEFAULT 0,
    hearing_at      TEXT,                       -- YYYY-MM-DD HH:MM
    hearing_date    TEXT,                       -- YYYY-MM-DD
    time_estimated  INTEGER DEFAULT 0,          -- 开庭时间是否为推算
    courtroom       TEXT,
    claim_amount    REAL,                       -- 涉案金额（元）
    amount_basis    TEXT,
    notice_type     TEXT,
    publish_date    TEXT,
    content         TEXT,
    content_hash    TEXT,
    parse_quality   REAL,
    raw_json        TEXT,
    fetched_at      TEXT,
    parsed_at       TEXT,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_hearings_date  ON hearings(hearing_date);
CREATE INDEX IF NOT EXISTS idx_hearings_court ON hearings(court);
CREATE INDEX IF NOT EXISTS idx_hearings_prov  ON hearings(province);
CREATE INDEX IF NOT EXISTS idx_hearings_def   ON hearings(defendant_type);
CREATE INDEX IF NOT EXISTS idx_hearings_amt   ON hearings(claim_amount);

-- ── 执行悬赏公告 ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT,
    court           TEXT,
    province        TEXT,
    city            TEXT,
    case_no         TEXT,
    debtor          TEXT,                       -- 被执行人
    debtor_count    INTEGER DEFAULT 1,
    subject_amount  REAL,                       -- 执行标的（元）
    reward_ratio    REAL,                       -- 奖励比例，0.05 = 5%
    reward_cap      REAL,                       -- 封顶金额（元）
    reward_fixed    REAL,                       -- 固定赏金（元）
    reward_est      REAL,                       -- 预估最高奖励（元）
    reward_type     TEXT,                       -- property / whereabouts / both
    publish_date    TEXT,
    valid_from      TEXT,
    valid_to        TEXT,
    contact_phone   TEXT,
    content         TEXT,
    content_hash    TEXT,
    parse_quality   REAL,
    raw_json        TEXT,
    fetched_at      TEXT,
    parsed_at       TEXT,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_notices_pub    ON notices(publish_date);
CREATE INDEX IF NOT EXISTS idx_notices_amt    ON notices(subject_amount);
CREATE INDEX IF NOT EXISTS idx_notices_est    ON notices(reward_est);
CREATE INDEX IF NOT EXISTS idx_notices_prov   ON notices(province);
CREATE INDEX IF NOT EXISTS idx_notices_court  ON notices(court);

-- ── 导入/采集台账 ─────────────────────────────────────────
-- 管理员上传一次、或采集器跑一次，都要在这里留痕：
-- 什么时候、从哪来、读了多少、新进多少、改了多少、失败原因。
CREATE TABLE IF NOT EXISTS ingest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    kind        TEXT,                           -- tender / hearing / bounty / auto
    mode        TEXT,                           -- db / jsonl / csv / collector
    origin      TEXT,                           -- 文件名或来源库路径
    total_seen  INTEGER DEFAULT 0,
    inserted    INTEGER DEFAULT 0,
    updated     INTEGER DEFAULT 0,
    skipped     INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running',         -- running / ok / failed
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_time ON ingest_runs(started_at);

-- ── 采集失败台账 ───────────────────────────────────────────
-- 哪个采集器、哪个数据源、什么原因挂了。后台首页直接展示这张表，
-- 修规则时按 source 分组看，一眼知道该改哪个 yaml。
CREATE TABLE IF NOT EXISTS crawl_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    run_id      TEXT,                           -- 同一轮采集的批次号
    board       TEXT,                           -- tender / hearing / bounty
    source      TEXT,                           -- 站点标识；整体失败时是 (整体)
    stage       TEXT,                           -- list / detail / parse / network / import
    reason      TEXT,                           -- 一句话原因
    detail      TEXT,                           -- 原始错误（截断到 500 字）
    resolved    INTEGER DEFAULT 0               -- 修好后手动置 1，后台就不再标红
);
CREATE INDEX IF NOT EXISTS idx_fail_time   ON crawl_failures(started_at);
CREATE INDEX IF NOT EXISTS idx_fail_source ON crawl_failures(source);
CREATE INDEX IF NOT EXISTS idx_fail_open   ON crawl_failures(resolved, started_at);


-- ── 法院通讯录 ─────────────────────────────────────────────
-- 法院名称 / 地址 / 邮编 / 电话。数据源是 tool.110.com/fy.php（全国法院地址电话）。
--
-- 为什么单独一张表：hearings / notices 里的 court 只是一个名字（用于分组），
-- 而「这家法院在哪、打哪个电话联系」是另一份资料，两类数据的变化节奏也不同
-- （公告每天变，法院地址几年不变）。分表存，两边各不污染。
--
-- 覆盖缺口如实记录：源站整整缺海南、宁夏两个省的法院（其页内搜索也是坏的，
-- 返回 SQL 报错）。这两个省在 coverage 里留一行 status='missing'，
-- 不静默跳过——「没有」和「没抓到」是两件事，页面上必须能区分。
CREATE TABLE IF NOT EXISTS courts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,              -- 法院全称，如 广州市越秀区人民法院
    short           TEXT,                       -- 去掉「人民法院」的短名
    level           TEXT,                       -- 高院 / 中院 / 基层 / 特殊 / NULL
    province        TEXT,                       -- 省级行政区（归一后，如 广东省）
    city            TEXT,
    address         TEXT,
    postcode        TEXT,                       -- 邮编
    phone           TEXT,                       -- 源站原文，可能含「（昼）（夜）」多号
    phone_day       TEXT,                       -- 拆出来的白天号码
    phone_night     TEXT,                       -- 拆出来的夜间号码
    source          TEXT NOT NULL,              -- fy110 / …
    source_id       TEXT NOT NULL,              -- 源站 id
    source_url      TEXT,
    fetched_at      TEXT,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_courts_name ON courts(name);
CREATE INDEX IF NOT EXISTS idx_courts_prov ON courts(province);
CREATE INDEX IF NOT EXISTS idx_courts_city ON courts(city);
CREATE INDEX IF NOT EXISTS idx_courts_lvl  ON courts(level);

-- 每个省抓到了多少、缺了哪些。用来回答「这个源到底全不全」，
-- 而不是让页面用「共 N 家」假装覆盖了全国。
CREATE TABLE IF NOT EXISTS court_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    province        TEXT NOT NULL,
    province_code   TEXT,                       -- 源站的省份编号
    claimed         INTEGER,                    -- 源站该省声称的条数
    fetched         INTEGER DEFAULT 0,          -- 实际入库条数
    status          TEXT,                       -- ok / missing / failed
    note            TEXT,
    checked_at      TEXT,
    UNIQUE(source, province)
);


-- 版本号由 manage.py 在 ensure_schema 里写，不在这里硬编码，
-- 否则每次重复执行 schema 都会把版本号冲回去。
