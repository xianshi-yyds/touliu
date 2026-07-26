# ExhibitFlow 本机抖音抓取助手

这个扩展是“目标主机账号抓取”链路的一部分：

```text
employee.xianshi.icu
  -> 识别当前 Chrome 配置的 worker_id
  -> 新服务器创建并保存任务
  -> 扩展在当前 Chrome 中打开抖音搜索页
  -> 使用当前浏览器登录态请求抖音搜索接口
  -> 结果回传新服务器
```

## 本地安装

1. 在目标电脑的 Chrome 中登录抖音。
2. 打开 `chrome://extensions/`，开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择本目录 `browser_extension/`。
4. 刷新 `https://employee.xianshi.icu/`。
5. 找爆款员工的“抓取方式”选择“目标主机账号抓取”。

扩展不会上传 Cookie；它只在当前 Chrome 的抖音页面上下文中执行检索，并把公开视频元数据回传服务器。普通网页无法直接控制另一个标签页，因此目标电脑必须安装并启用这个扩展。
