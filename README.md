# 🍷 酒馆 (SillyTavern) Gemini 智能分池多号轮询反代 (`aistudioapi`)

专为 **SillyTavern (酒馆)** 打造的 Google AI Studio / Gemini 免翻墙直连反代服务。  
自带中文可视化管理面板、多 Key 自动轮询、**💎 赠金/Pro池 与 ⚡ 免费/Flash池 智能分流**、以及 Render 自动保活。

---

## 🚀 快速部署

点击下方按钮即可一键免费部署你自己的专属反代（无需懂代码）：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)
[![Deploy on Zeabur](https://zeabur.com/button.svg)](https://zeabur.com/templates)

> 💡 **一键部署超简单步骤**：
> 1. 点击上方的 **Deploy to Render** 按钮（如果你是 Fork 的仓库，或者直接点按钮并在输入框粘贴本仓库的 GitHub 地址）。
> 2. 在弹出的页面中：
>    - **Blueprint Name** 随便填个名字（例如 `my-gemini`）。
>    - **`ADMIN_PASSWORD`** 填入你自己设定的**面板登录密码**（也是酒馆连接时填的密码）。
> 3. 点击 **Apply**，等待 1~2 分钟部署完成，打开生成的 `https://xxx.onrender.com` 网址即可进入中文管理面板！

---

## ✨ 核心功能亮点

1. **完美兼容新版 `AQ.` 开头与 `AIza` 开头密钥**：
   - 底层内置 `?key=` 原生通道转换器，彻底解决 Google 官方 `/v1beta/openai/` 接口对 `AQ.` 密钥报 `401` 的 Bug。
2. **💎 赠金/Pro池 vs ⚡ 免费/Flash池 智能省钱分流**：
   - 酒馆选 **Pro 模型**时：只调度【💎 赠金/Pro池】里的号，绝不拿免费号去撞 429。
   - 酒馆选 **Flash 模型**时：优先白嫖【⚡ 免费/Flash池】里的免费号，自动省下宝贵的赠金！免费号用完还会自动用赠金号无缝兜底。
   - 支持面板内**一键测活 & 自动识别**哪个 Key 有赠金、哪个是免费层。
3. **双协议支持（酒馆怎么连都行）**：
   - **Google AI Studio 原生模式（推荐）**：酒馆选 `Google AI Studio`，反向代理填 `https://你的网址.onrender.com`。
   - **OpenAI 兼容模式**：酒馆选 `Custom (OpenAI-compatible)`，地址填 `https://你的网址.onrender.com/v1`。
4. **安全锁 & 防丢号备份 & 自动保活**：
   - 强制校验面板密码，防止别人扫到网址偷刷你的赠金。
   - 面板支持**点击直接修改备注**、**一键复制 Render 环境变量**（粘回 Render 环境变量永久防重启丢失）、以及 **5 分钟自动唤醒保活**。
