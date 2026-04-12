# 中国足球赛季大数据监测系统 (CSL Dashboard 2026)

一个自动化、可视化的中国足球赛季数据看板，覆盖**中超 (CSL)**、**中甲 (CL1)** 及**中职联赛**。

## 🌐 在线访问

[https://YOUR_USERNAME.github.io/csl-dashboard-2026/](https://YOUR_USERNAME.github.io/csl-dashboard-2026/)

## ✨ 核心功能

- **多联赛支持**：中超、中甲、中职联赛数据一体化展示
- **实时积分榜**：自动计算积分，支持行政处罚（扣分）逻辑
- **比赛事件流**：进球、红黄牌、换人等详细事件时间轴
- **球员统计**：进球榜、助攻榜、红黄牌榜，配合 Chart.js 雷达图可视化
- **自动更新**：每日凌晨 2 点自动抓取最新数据并部署

## 📌 GitHub Pages 首次配置（必做）

若 Actions 在 `configure-pages` 步骤报 **Get Pages site failed / Not Found**，说明仓库还未启用 Pages 或未使用 Actions 发布：

1. 打开 GitHub 仓库 → **Settings** → **Pages**
2. **Build and deployment** 里将 **Source** 选为 **GitHub Actions**（不要选 “Deploy from a branch” 作为长期方案，否则与本 workflow 不匹配）
3. 保存后回到 **Actions** 重新运行工作流

可选：若使用 **Environment** `github-pages` 且首次需审批，请到 **Settings → Environments → github-pages** 查看保护规则。

官方说明：<https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site#publishing-with-a-custom-github-actions-workflow>

## 🚀 技术栈

- **爬虫**: Playwright (Python)
- **数据处理**: Python (pandas)
- **前端**: HTML5, Tailwind CSS, Chart.js, Vanilla JavaScript
- **部署**: GitHub Pages + GitHub Actions

## 📁 项目结构

```
csl_project_v2/
├── .github/workflows/    # GitHub Actions 自动化部署
├── data/                 # 数据文件 (本地存储，不上传)
├── src/
│   ├── crawler/          # 爬虫模块
│   ├── processor/        # 数据处理模块
│   └── renderer/         # 页面渲染模块
├── web/                  # 生成的静态网页 (部署到 GitHub Pages)
├── requirements.txt      # Python 依赖
└── README.md            # 项目说明
```

## 🛠️ 本地开发

```bash
# 1. 克隆项目
git clone https://github.com/YOUR_USERNAME/csl-dashboard-2026.git
cd csl-dashboard-2026

# 2. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 3. 运行爬虫
python src/crawler/schedule_indexer.py
python src/crawler/batch_event_crawler.py

# 4. 合并赛程与事件为统一索引（生成 data/all_seasons_unified_index.json，供下一步使用）
python src/processor/data_unifier.py

# 5. 处理数据（积分榜、事件补全等）
python src/processor/data_enricher.py

# 6. 生成页面
python src/renderer/web_renderer.py

# 7. 本地预览
cd web
python -m http.server 8080
```

## 📊 数据来源

- **主要数据源**: 懂球帝 (Dongqiudi)
- **补充数据源**: 中职联官方网站

## ⚠️ 注意事项

1. 本项目的数据抓取仅供**个人学习和研究**使用
2. 请勿用于商业用途
3. 请遵守目标网站的 robots.txt 协议
4. 请合理设置抓取频率，避免对目标网站造成压力

## 📝 许可证

MIT License

---

*Built with ❤️ by Nico + Cursor*
