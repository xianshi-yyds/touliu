# 爆款监控前端接入协议

当前系统只提供爆款监控的宿主入口，不实现抖音、小红书检索模块。外部模块挂载到：

```text
#viral-monitor-mount
```

打开左侧“爆款监控”时，页面会触发：

```js
window.addEventListener('exhibitflow:viral-monitor-open', (event) => {
  const {mount, expo_id, expo_name, bridge} = event.detail;
  // 在 mount 中渲染外部爆款监控应用。
});
```

也可以提前注册一个挂载对象：

```js
window.ExhibitFlowViralMonitorApp = {
  mount(element, context, bridge) {
    // element 是挂载容器，context 包含当前展会信息。
  },
};
```

用户在外部模块点击“生成同款”时，调用宿主桥接方法：

```js
window.ExhibitFlowViralMonitorHost.openCreator({
  reference_case_id: 'douyin-123',
  platform: 'douyin',
  title: '参考视频标题',
  video_url: 'https://example.com/reference.mp4',
  cover_url: 'https://example.com/cover.jpg',
  reference_analysis_id: '',
  reference_logic: {},
  reference_profile: {
    content_type: 'hybrid', // montage / avatar / hybrid
    confidence: 0.88,
    talking_head_ratio: 0.35,
    copy_style: {},
    visual_style: {},
  },
});
```

必填字段为 `reference_case_id` 或 `video_url` 中的一个。其余字段可以在后续视频解析模块完成后补充。`reference_profile` 是预留给外部 VL 解析服务的标准入口；当前项目不负责抓取和 VL 解析。宿主会打开生成视频页面，把参考视频显示在“参考案例”区域，并在提交生成任务时继续携带参考案例、解析逻辑和参考画像。

桥接成功后还会触发：

```text
exhibitflow:viral-reference-attached
```

外部模块不得直接读取或修改系统的 `.env`、任务存储和 Remotion 工程；只通过上述前端协议交付参考案例。
