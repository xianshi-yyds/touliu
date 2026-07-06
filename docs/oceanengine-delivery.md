# 巨量投放链路接入说明

当前独立版 ExhibitFlow 的投放员工分为三段：授权账户、创建投放对象、拉取报表。

## 1. 必填账号配置

在 `.env` 配置：

```env
OCEANENGINE_APP_ID=你的巨量应用 APP_ID
OCEANENGINE_SECRET=你的巨量应用 Secret
OCEANENGINE_REDIRECT_URI=http://localhost:8610/api/oceanengine/callback
```

巨量开放平台应用后台的“回调地址”必须与 `OCEANENGINE_REDIRECT_URI` 完全一致。若后台只填 `http://localhost:8610`，系统也兼容根路径回调；若后台仍是 `http://localhost:8501`，请把服务端口和回调地址统一成 8501，或去巨量后台改为上面的地址。

## 2. 授权账户

前端路径：`投放员工 -> 连接巨量账号 -> 打开巨量授权页`。

授权成功后系统会保存：

- `access_token`
- `refresh_token`
- `advertiser_id`
- `advertiser_name`

如果回调没有自动成功，可以展开“临时调试”，从回调 URL 中复制 `auth_code` 或 `code`，点击“用授权码换 Token”。

## 3. 创建真实投放对象

真实投放目前按官方 JSON 透传，避免平台行业/目标/账户差异导致字段遗漏：

1. 上传视频素材：`/open_api/2/file/video/ad/`，返回 `video_id`。
2. 创建项目：`/open_api/v3.0/project/create/`，返回 `project_id`。
3. 创建单元/推广：`/open_api/v3.0/promotion/create/`，引用 `project_id` 和素材 `video_id`。

前端提供三个按钮分别对应三步。项目和单元 JSON 建议先在巨量官方“API 参数生成/调试工具”里生成，再粘贴到系统。

## 4. 拉取投放报表

接口：`/open_api/v3.0/report/custom/get/`

默认拉取字段：

- `stat_cost`：消耗
- `show_cnt`：展示
- `click_cnt`：点击数
- `convert_cnt`：转化数
- `play_cnt`：播放量
- `valid_play_cnt`：有效播放数

系统计算：

- CPM = 消耗 / 展示 * 1000
- CTR = 点击数 / 展示 * 100%
- 平均转化成本 = 消耗 / 转化数
- 转化率 = 转化数 / 点击数 * 100%
- 有效播放量率 = 有效播放数 / 播放量 * 100%

## 5. 当前未能自动完成的原因

如果未完成授权，所有真实上传、创建、报表任务都会失败并提示“缺少 Access Token”。这不是代码链路失败，而是巨量 OAuth 还没拿到授权 token。

## 6. 小白模式：用户只点授权

前端已新增“巨量投放开通向导”。普通用户不需要理解 Token、广告主 ID 或账号角色：

1. 点击“开始授权”。
2. 授权完成后回到页面，系统自动检测：
   - 系统应用是否已配置；
   - 是否拿到 OAuth Token；
   - 是否拿到真正可投放的广告主账户；
   - 报表接口是否可查询。
3. 如果检测失败，页面会显示具体原因和下一步入口。

当前测试账号的诊断结果是：OAuth 已成功，但授权到账户角色 `PLATFORM_ROLE_ENTERPRISE_BP_ADMIN`，不是投放广告主账户，因此需要在巨量后台开通/共享真正广告主账户后重新授权。
