# 🍷 酒馆 (SillyTavern) 专属 Gemini / Vertex 赠金智能分池轮询反代

一个专为 **SillyTavern（酒馆）** 打造的超轻量、零门槛、高颜值 **Google Gemini API & Vertex AI 赠金多号轮询反向代理**。支持一键部署到 Render，自带全中文可视化管理面板（完美适配电脑与手机端卡片布局）。

---

## ✨ 核心亮点

1. **🌐 网页一键 Google 授权登录（免开黑框 / 全自动查项目 ID）**
   - 直接在管理面板点击按钮登录 Google 账号、粘贴授权码，后台**全自动扫描账号下的 GCP 项目、自动开启 Vertex AI 与 Gemini API 服务并生成永久刷新凭证 (`GCLI:`)**，彻底告别繁琐的命令行和手填项目 ID！
2. **🏛️ Vertex 赠金通道 / 🌱 普通免费通道 随心一键切换**
   - **Vertex 赠金通道**：绕过 AI Studio 预充值限制，直接消耗 Google Cloud 里的 **$40 / $300 试用赠金**，满血畅玩 `gemini-3.1-pro-preview`、`gemini-2.5-pro`、`gemini-3.8-flash` 等全系列模型！
   - **普通 AI Studio 通道**：支持纯免费层级小号（或有赠金但不想扣赠金时），一键切回普通通道白嫖免费 Flash 额度。
3. **💎 赠金/Pro 池 & ⚡ 免费/Flash 池 智能分池调度**
   - 请求 **Pro 模型** 时：精准调度【💎 赠金/Pro池】，绝不拿免费号去撞 429 浪费时间。
   - 请求 **Flash 模型** 时：**优先白嫖【⚡ 免费/Flash池】**，免费号全忙或限流时自动无缝由【💎 赠金/Pro池】兜底！
4. **🛡️ 酒馆专属防截断 & 破限优化**
   - **强制解除敏感审查**：全自动为所有请求注入 5 项 `BLOCK_NONE` 安全策略。
   - **防无预设开头吞字**：自动保底 `maxOutputTokens >= 8192`，给足 Gemini 内置推理（Thinking）空间，不挂预设也不会被截断开头。
   - **纯净思维链（CoT）兼容**：原生模式支持酒馆自动折叠思维链；OpenAI 兼容模式自动过滤底层 `thought: true` 隐藏块，避免污染正文。
5. **🔌 双协议 100% 兼容 & 强制安全密码锁**
   - 同时支持酒馆 **Google AI Studio 原生模式** 与 **Custom (OpenAI 兼容模式)**。
   - 所有接口强制校验管理密码，防止反代网址泄露后被他人盗刷赠金。
6. **📱 完美手机端适配 & 防重启丢号导出**
   - 手机端自动切换为清爽的独立卡片布局，告别表格竖排挤压。
   - 支持**一键复制 `PRO_API_KEYS` / `FREE_API_KEYS` 环境变量**，粘贴到 Render 后台后永久不怕容器重启。

---

## 🚀 3 分钟一键部署到 Render（零代码基础）

1. **Fork 本仓库** 到你自己的 GitHub 账号下。
2. 登录 [Render 控制台](https://dashboard.render.com/)，点击右上角 **`New +`** -> **`Web Service`**，连接你刚 Fork 的仓库。
3. Render 配置项保持默认（或按以下填写）：
   - **Language / Runtime**：`Python 3`（或 `Docker`）
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**：选择 **`Free`**
4. 在下方 **Environment Variables（环境变量）** 添加：
   - `ADMIN_PASSWORD`：填入你自己设定的**管理密码**（既是网页后台登录密码，也是酒馆连接时填的 API Key 密码，默认初始为 `123456`，**强烈建议修改！**）
5. 点击 **`Create Web Service`**，等待 1~2 分钟显示 `Live` 即可打开你的专属反代网址！

---

## 🔑 如何添加账号 / Key？（两种方式任选）

打开你的 Render 反代网址并输入管理密码登录：

### 方式一：🌐 网页一键授权登录（最推荐！支持赠金号 & 免费小号）
1. 在面板点击 **「1️⃣ 第一步：点击打开 Google 登录授权页」**（下拉框可选「自动检测」、「强制走 Vertex 赠金通道」或「强制走普通 AI Studio 通道」）。
2. 登录 Google 账号，遇到权限勾选页面时**勾上“全选”**并继续。
3. 将网页最后显示的一串 `4/0A...` 授权码复制粘贴回面板的 **第 2 步输入框**。
4. 点击 **「3️⃣ 一键自动查项目 & 入池」**，等待几秒即自动完成项目绑定与通道激活！

### 方式二：➕ 批量粘贴 `AIza...` / `AQ...` / `GCLI:...` 密钥
- 直接在面板下方的批量输入框中一行一个粘贴即可，支持在末尾加 `#备注名称`。

### 💡 随时切换是否消耗赠金 & 永久备份
- 在面板的「当前 Key 轮询池」列表中，直接**点击紫色的 `🏛️ Vertex赠金通道` 或绿色的 `🌱 普通AIStudio通道` 标签**，即可随时切换该账号是否消耗 GCP 赠金！
- 绑定或修改完通道后，点击列表上方的 **「📋 一键复制 Render 环境变量」**，把 `PRO_API_KEYS` 和 `FREE_API_KEYS` 粘贴到 Render 的 `Environment` 页面保存，以后就算容器重启也永远不会丢号！

---

## 🍷 酒馆 (SillyTavern) 连接填写指南

### 推荐方式 A：Google AI Studio 原生模式（支持完整原生参数）
1. 打开酒馆顶部的 **插头图标 (API Connections)**。
2. **API** 选择：`Chat Completion`，**Chat Completion Source** 选择：`Google AI Studio`。
3. 展开 **Reverse Proxy (反向代理)**：
   - **Proxy URL**：填入你的反代根网址，例如 `https://你的服务名.onrender.com`（**末尾不要加 `/v1`**）
   - **Proxy Password**：填入你的**面板管理密码**
4. 点击 **Connect** 即可拉取完整模型列表（含 `gemini-3.1-pro-preview`、`gemini-2.5-pro`、`gemini-3.8-flash`、`gemini-2.5-flash` 等）！

### 备选方式 B：OpenAI 兼容模式
1. **Chat Completion Source** 选择：`Custom (OpenAI-compatible)`。
2. **Custom Endpoint (Base URL)**：填入 `https://你的服务名.onrender.com/v1`（**末尾带 `/v1`**）。
3. **Custom API Key**：填入你的**面板管理密码**。
4. 点击 **Connect** 即可开始愉快玩耍！
