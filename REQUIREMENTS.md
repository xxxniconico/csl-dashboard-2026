# 中国足球赛季大数据监测系统 (CSL Dashboard v2.1) - 需求文档

## 1. 项目目标
构建一个具备**深度业务逻辑**的中国足球赛季监测系统。系统不仅要展示静态的积分榜，更要能够通过**比赛事件流 (Match Event Stream)** 还原比赛过程，并能够处理**行政处罚（如扣分）**等复杂的业务规则，实现高度动态、可交互、可溯源的专业级足球数据看板。

## 2. 核心业务逻辑 (Business Logic)

### 2.1 核心数据实体关系
系统必须建立以下层级关联：
- **联赛 (League)** $\rightarrow$ **赛季 (Season)** $\rightarrow$ **比赛 (Match)**
- **比赛 (Match)** $\rightarrow$ **事件 (Event)** (包含：进球、红黄牌、换人、伤停)
- **比赛 (Match)** $\rightarrow$ **场地 (Venue)** (包含：体育场名称、城市)
- **比赛 (Match)** $\rightarrow$ **参与方 (Participants)** (主队、客队、裁判)
- **事件 (Event)** $\rightarrow$ **球员 (Player)** (关联进球者、受罚者)
- **行政指令 (Administrative Action)** $\rightarrow$ **积分修正 (Point Adjustment)** (处理扣分、罚分)

### 2.2 关键业务规则
- **积分计算引擎 (Points Engine)**：
    - 基础规则：胜(3), 平(1), 负(0)。
    - **修正规则**：必须支持 `penalty_points` 字段。最终积分 = $\sum(\text{Match Points}) - \text{Administrative Penalties}$。
- **比赛数据颗粒度**：
    - 必须记录每场比赛的详细 `events` 列表。
    - 必须支持按 `venue` (体育场) 和 `club` (俱乐部) 进行多维度筛选。
- **数据实时性**：
    - 必须能够处理“比赛进行中”与“比赛已结束”两种状态。

## 3. 升级后的数据架构 (Data Schema)

### 3.1 统一标准 JSON 结构 (`csl_normalized.json`)
```json
{
  "season": "2025-2026",
  "leagues": [
    {
      "league_id": "csl",
      "name": "中超联赛",
      "standings": [
        {
          "club_id": "club_001",
          "club_name": "上海海港",
          "points": 30, 
          "penalty_points": 3, 
          "effective_points": 27,
          "played": 10,
          "w_d_l": [7, 2, 1],
          "summary": { "goals_for": 25, "goals_against": 10 }
        }
      ],
      "matches": [
        {
          "match_id": "m_2026_001",
          "date": "2026-04-01",
          "venue": { "name": "上海体育场", "city": "上海" },
          "home_club": "上海海港",
          "away_club": "北京国安",
          "status": "finished",
          "score": {"home": 2, "away": 1},
          "events": [
            {"type": "goal", "player": "武磊", "minute": 23},
            {"type": "yellow_card", "player": "张衡", "minute": 45},
            {"type": "red_card", "player": "李宇", "minute": 78}
          ]
        }
      ]
    }
  ]
}
```

## 4. 待办事项 (Todo)
- [ ] **[Phase 1: Data Model]** 完成 `csl_normalized.json` 结构的重构与数据填充。
- [ ] **[Phase 2: Crawler]** 升级爬虫逻辑，开始抓取 `events` (进球、红黄牌) 细节。
- [ ] **[Phase 3: Processor]** 实现 `Points Engine`，支持处理 `penalty_points` 逻辑。
- [ ] **[Phase 4: Renderer]** 重新设计前端，支持按 `venue` 和 `club` 进行联动筛选。
