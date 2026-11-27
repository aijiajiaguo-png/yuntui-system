# 云推分发系统 v1

在 Muse 的电脑上已搭好并测试通过。短链跳转 + 日志统计 + 看板，纯 Python 标准库，零依赖。

## 现在的状态

- 服务正在运行：`http://localhost:8080/`（仅本机可访问）
- 测试已通过：建短链 → 302 跳转 → 写日志 → 统计 PV/UV/渠道 → 落地页 curl 验活
- 管理 token：见 `config.json`（首次打开看板时会问，填这个）

## 文件

| 文件 | 作用 |
|---|---|
| `app.py` | 短链服务 + 统计 API + 验活（核心） |
| `dashboard.html` | 手机看板：今日数据、各线进度、新建短链、短链管理 |
| `config.json` | 端口、token、预估转存系数、下一批 |
| `data/links.json` | 短链表（短链码 → 真实推广链接） |
| `data/tasks.json` | 各线今日任务定额（照着截图里的 11 条线建的） |
| `logs/api-access.log` | JSON 行日志（轮转成 .gz，与截图一致） |
| `run.sh` | 一键启动 |

## 操作（看板上都能点）

- 新建短链：填标题、真实推广链接、渠道、账号 → 得到 `/r/xxxx`，发帖就贴这个
- 各线进度：每发一条点 +1，点错了点 -1
- 失效提醒：看板每小时 curl 检查落地页，打不开的标"失效"
- 搜索：按账号名、短链码、帖子标题查

## 为什么现在还没法给外人点

本机在沙箱里，出站只走 HTTP(S) 代理，免费隧道逐一试过，均被拦：

- Cloudflare 快速隧道：`api.trycloudflare.com` 经代理返回 403
- SSH 隧道（localhost.run / serveo 类）：出站 TCP 直连被重置
- localtunnel：`server.localtunnel.me` 经代理连不上（000）
- ngrok：dashboard 和 api 都能通（302/400），官方程序库已装好并加载成功，
  但实测连接时报 TLS 握手失败——代理对 ngrok 的隧道服务器（tunnel.ngrok.com）
  直接给空回复（exit 52），属于出口白名单拦截，**有 authtoken 也连不上**。
  结论：从本机出发的免费隧道全灭（Cloudflare 403、localtunnel 服务器不通、
  SSH 被重置、ngrok 隧道服务器被拦）。要公开访问只能换路：把程序部署到
  免费托管（Render/PythonAnywhere 免费档，需用户注册账号）或买轻量服务器。

服务本身是好的，只差一个公网出口。

## 上线办法（二选一）

### A. 买台轻量云服务器（推荐，稳定）

1. 腾讯云 / 阿里云买轻量服务器 2核2G（新人价 100–200 元/年）+ 一个域名（约 60 元/年）
2. 服务器上装 Python 3，`git` 或直接把本目录拷过去
3. `bash run.sh` 启动，Nginx 反代 8080 端口并配 HTTPS 证书
4. 以后短链就是 `https://你的域名/r/xxxx`，永久有效

### B. 免费 Cloudflare 命名隧道 + 域名

1. 注册免费 Cloudflare 账号，域名 DNS 托管到 Cloudflare
2. 建一条命名隧道指向本机 8080 端口（需要 `cloudflared login`，在你自己电脑上操作一次）
3. 优点：不用买服务器；缺点：本机重启要重连隧道，稳定性不如 A

### C. 免费托管部署（Render，已备好，只差账号）

本目录已备好 `render.yaml` / `requirements.txt` / `.gitignore`，程序也已改成
读 `PORT` 环境变量（托管平台要求）。步骤：

1. 注册 GitHub 账号，把本目录推成一个仓库（或让 Muse 帮你整理成部署包）
2. 注册 render.com 免费账号，New → Web Service → 选这个仓库
3. Render 按 `render.yaml` 自动部署，得到 `https://yuntui-system.onrender.com`
   这样的公开地址，看板和短链都走它
4. 注意：免费档 15 分钟没人访问会休眠，下一次点短链要等几秒唤醒；
   每月 750 小时免费额度，单实例够用。数据存在服务器磁盘上，
   免费档重启不丢文件，但 Render 重新部署会清空——重要数据定期导出备份。

### D. 免费托管部署（PythonAnywhere，不用 GitHub，邮箱注册就行）

本目录已备好 `wsgi.py`（WSGI 入口，详细步骤写在文件开头的注释里）。步骤：

1. 注册 pythonanywhere.com 免费账号（邮箱 + 密码，不用银行卡）
2. Files 页上传 `yuntui-deploy.zip` 后解压，记下路径
   （如 `/home/<用户名>/yuntui-system`）
3. Web 页新建免费 Web App，Python 选 3.12，框架选 Manual configuration
4. 打开 WSGI configuration file，清空后改成：
   `import sys; sys.path.insert(0, '/home/<用户名>/yuntui-system'); from wsgi import application`
5. Web 页 Environment variables 里加一条：`ADMIN_TOKEN=<你自己定的管理密码>`
6. 点 Reload，打开 `https://<用户名>.pythonanywhere.com` 就是看板
7. 注意：免费档每月 CPU 额度有限，访问量不大完全够用；数据存在服务器文件里，
   别删文件就行。

## 注意事项

- 预估转存 = 今日点击 PV × 系数（默认 0.041），定期用真实转存数校准系数
- 每天零点后统计自动按新日期算；任务进度每天手动清零（把 `data/tasks.json` 里 done 改回 0，
  或看板上 -1 点回去；后续版本可加自动清零）
- token 不要发给别人，知道 token 就能新建短链、改任务

## 轻量小店 v1（/shop，与 CPS 看板同站）
- 前台：`/shop`（商品、下单、USDT 付款、提交交易哈希、查单取码）
- 管理：`/shop/admin`（订单确认/发货、加卡密库存、商品管理、收款设置），与看板用同一个管理 token
- 首品：ChatGPT Plus 月卡｜菲区 CDK，售价 135 元（成本 110 元，待供应商回复后核对）
- 收款：USDT 手动确认（地址、网络、汇率在 config.json 的 "shop" 里或管理页设置）；确认收款后有库存自动发码
- 数据：`data/shop_products.json` / `data/shop_orders.json` / `data/shop_inventory.json`
