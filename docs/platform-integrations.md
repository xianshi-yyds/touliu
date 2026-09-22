# 多平台投放扩展

当前投放员工把「渠道选择」和「平台执行」拆开：任务先保存统一的素材、落地页、客户要求和策略，进入平台执行前再按渠道检查对应权限。这样小红书或视频号没有官方权限时，也不会误走抖音接口。

## 当前能力矩阵

| 平台 | 找爆款/公开检索 | 投放执行 | 数据/复盘 | 当前状态 |
| --- | --- | --- | --- | --- |
| 抖音 | TikHub；目标主机浏览器兜底 | 巨量引擎 Marketing API | 巨量报表 | 已有完整安全草稿链路 |
| 小红书 | TikHub / Rnote 公开笔记与账号数据；本机账号兜底 | 小红书官方营销 API 或人工复核 | 新榜外部控制台/官方权限 | TikHub 已接入，投放 API 保留权限边界 |
| 视频号 | 不做未确认的公开抓取；支持导入 | 腾讯广告 Marketing API | 腾讯广告或新榜 | 本次先接入创意只读 `/v1.3/adcreatives/get` |

## 环境变量

复制 `.env.example` 中的可选项到服务端 `.env`，不要把密钥提交到 Git：

```bash
RNOTE_API_KEY=...
RNOTE_BASE_URL=https://rnote.dev/api/v2/crawler

TENCENT_ADS_BASE_URL=https://api.e.qq.com
TENCENT_ADS_ACCESS_TOKEN=...
TENCENT_ADS_REFRESH_TOKEN=...
TENCENT_ADS_CLIENT_ID=...
TENCENT_ADS_CLIENT_SECRET=...
TENCENT_ADS_ACCOUNT_ID=...
```

配置后重启 API，`GET /api/config` 会返回不含密钥的平台能力状态。

## 小红书公共检索链路

找爆款员工选择「小红书 + 服务器公共检索」时，有 Rnote Key 则优先调用 Rnote；否则使用已有 TikHub Key 调用 App V2 `search_notes`，按热度搜索视频笔记并完成分页、去重和统一互动分。TikHub 还通过 `GET /api/v1/xiaohongshu/app_v2/get_user_info` 与 `get_user_posted_notes` 获取公开账号资料和作品，项目统一封装为 `POST /api/social/profile`。这些链路都不依赖浏览器登录态，深度检索最多 5 页，避免失控扣费。

## 腾讯广告/视频号链路

投放员工选择「视频号」后，当前可以保存渠道化的投放方案，并在账号设置中读取腾讯广告创意库。读取接口是只读的：不会创建广告、不上传素材、不启动消耗。正式创建项目/单元前仍需要补齐腾讯 OAuth、广告主权限和字段映射，再单独实现 create/update/report 适配器。

## 尚未自动化的部分

- 小红书广告投放需要官方营销 API 的应用权限、广告主授权和具体账户字段；在拿到正式接口权限前，系统只做方案准备和官方入口跳转。
- 视频号自然发布和账号内容抓取没有在本项目中假设一个未确认的通用公开接口；当前以腾讯广告投放、创意读取和人工导入为主。
- 新榜 `xs.newrank.cn` 当前作为外部分析入口保留，不在服务端自动登录或抓取；后续若取得官方 API/企业授权，再接入报表同步。
