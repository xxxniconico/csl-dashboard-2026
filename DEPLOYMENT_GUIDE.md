# 🚀 GitHub Pages 部署指南

## 前提条件
- 你已经有一个 GitHub 账号
- 你已安装 Git 并配置了 SSH 或 HTTPS 认证

---

## 第一步：在 GitHub 创建仓库

1. 访问 [github.com/new](https://github.com/new)
2. 仓库名称：`csl-dashboard-2026`
3. 可见性：**Public** (GitHub Pages 免费部署需要公开仓库)
4. **不要** 初始化 README、.gitignore 或 license (我们已经有了)
5. 点击 **"Create repository"**

---

## 第二步：关联远程仓库并推送

在你的本地终端执行以下命令：

```bash
cd csl_project_v2

# 添加远程仓库 (替换 YOUR_USERNAME 为你的 GitHub 用户名)
git remote add origin https://github.com/YOUR_USERNAME/csl-dashboard-2026.git

# 重命名分支为 main
git branch -M main

# 推送到 GitHub
git push -u origin main
```

---

## 第三步：启用 GitHub Pages

1. 进入你的 GitHub 仓库页面
2. 点击 **Settings** (设置)
3. 左侧菜单选择 **Pages**
4. 在 **Build and deployment** 部分：
   - **Source**: 选择 `GitHub Actions` (不是 Deploy from a branch)
5. 等待几秒钟，页面会自动识别我们的 `deploy.yml` 工作流

---

## 第四步：手动触发首次部署

1. 在 GitHub 仓库页面，点击 **Actions** 标签
2. 点击左侧的 **"Deploy CSL Dashboard to GitHub Pages"** 工作流
3. 点击 **"Run workflow"** 按钮
4. 选择 `main` 分支，点击 **"Run workflow"**
5. 等待约 3-5 分钟，直到部署显示为绿色 ✅

---

## 第五步：访问你的看板

部署成功后，你会在 Pages 设置页面看到访问地址：

```
https://YOUR_USERNAME.github.io/csl-dashboard-2026/
```

---

## 🔄 自动更新机制

你的看板现在已经配置了**每日自动更新**：

- **更新时间**: 每天凌晨 2:00 (北京时间)
- **触发条件**: 
  - 定时任务 (每天 18:00 UTC)
  - 手动触发 (在 Actions 页面点击 "Run workflow")
  - 代码推送 (push 到 main 分支)

---

## ⚠️ 重要注意事项

### 1. 数据文件不上传
`.gitignore` 已配置为**忽略 `data/` 目录下的 JSON 文件**，因为：
- 这些数据文件体积较大
- 每次部署时 GitHub Actions 会重新抓取最新数据
- 避免敏感数据泄露

### 2. 首次部署可能需要手动触发
GitHub Pages 的自动部署可能需要手动触发第一次才能激活。

### 3. 检查 Action 运行日志
如果部署失败，请在 **Actions** 标签页查看运行日志，常见错误：
- Playwright 安装失败 → 检查 `playwright install chromium --with-deps`
- 爬虫被反爬 → 检查目标网站是否可访问
- Python 依赖缺失 → 检查 `requirements.txt`

---

## 🛠️ 故障排查

### 问题：Pages 显示 404
**解决方案**：
1. 确认 `web/` 目录存在于仓库根目录
2. 确认 `web/index.html` 存在
3. 在 Actions 页面重新运行部署工作流

### 问题：部署成功但页面是空白
**解决方案**：
1. 打开浏览器开发者工具 (F12)
2. 检查 Console 是否有 JavaScript 错误
3. 检查 Network 是否有资源加载失败
4. 确认 `index.html` 中的数据 JSON 路径正确

### 问题：数据不更新
**解决方案**：
1. 在 Actions 页面手动触发一次部署
2. 检查爬虫日志，确认数据抓取成功
3. 确认 `batch_event_crawler.py` 能正常抓取 events

---

## 📞 需要帮助？

如果遇到问题，请检查：
1. GitHub Actions 运行日志
2. 本仓库的 `README.md`
3. 联系项目维护者

---

*祝你部署顺利！⚡*
