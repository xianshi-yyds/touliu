# Tripo3D 3D 展品展厅 — 接入方案

## 一、概述

将 Tripo3D 的 AI 3D 生成能力接入 exhibitflow-lite 系统，为参展商提供"上传产品照片 → 自动生成 3D 模型 → 生成展示链接"的能力。

## 二、API 信息

- **Base URL:** `https://openapi.tripo3d.ai/v3`
- **Endpoint:** `POST /generation/image-to-model`
- **认证:** `Authorization: Bearer <API_KEY>`
- **模式:** 异步任务（提交 → 轮询 → 获取结果）

### 展会场景推荐配置

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `model` | `v3.1-20260211` | 最新模型，最佳质量 |
| `texture` | `true` | 生成纹理贴图 |
| `pbr` | `true` | 启用 PBR 材质（base_color, metallic, roughness, normal） |
| `texture_quality` | `detailed` | 平衡质量和速度 |
| `enable_image_autofix` | `true` | 自动优化低质量输入图片 |
| `orientation` | `align_image` | 模型对齐输入图片视角 |
| `face_limit` | `50000`~`100000` | Web 展示级别面数 |

### 响应字段

- `task_id`: 任务 ID，用于轮询
- `status`: 任务状态（pending/processing/success/failed）
- `output.model_url`: GLB 格式 3D 模型下载链接
- `output.rendered_image_url`: 预览图 URL
- `credits_consumed`: 消耗的 credits

## 三、两种集成路径

### 路径 A：轻量集成（推荐先做）

1. 参展商在"素材产出"页面上传产品照片
2. 后端调 Tripo3D API 生成 3D 模型
3. 获取 `model_url` 和 `rendered_image_url`
4. 将预览链接生成二维码，贴到宣传视频末尾
5. 视频文案加引导："扫码查看 3D 模型"

**优点：** 开发量小，不增加视频渲染复杂度，快速上线
**缺点：** 3D 展示和视频分离，需要用户扫码跳转

### 路径 B：深度集成（后续迭代）

1. 参展商上传产品照片
2. 后端调 Tripo3D API 生成 GLB 模型
3. 用 Three.js 服务端渲染旋转动画帧序列（360° 旋转，约 3-5 秒）
4. 帧序列作为 Remotion 素材合成到视频中
5. 视频里直接展示 3D 模型的旋转动画

**优点：** 效果惊艳，3D 展示直接在视频里呈现
**缺点：** 渲染时间长（Three.js 服务端渲染需要 GPU），成本高

## 四、系统架构设计

### 前端模块位置

在"素材产出"页面的"AI 补充"模块中新增"3D 展品"开关（类似数字人开关）。
开启后显示上传产品照片的入口。

### 后端 API

```
POST /api/tripo/upload-image     → 上传产品图片，返回 file_token
POST /api/tripo/generate-model   → 提交 Image-to-3D 任务
GET  /api/tripo/task-status/:id  → 轮询任务状态
GET  /api/tripo/model/:id        → 获取生成结果
POST /api/tripo/generate-qrcode  → 将预览链接生成二维码图片
```

### 数据库表

```sql
CREATE TABLE tripo_models (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id),
  product_name TEXT,
  input_image_url TEXT,
  task_id TEXT UNIQUE,
  status TEXT DEFAULT 'pending',
  model_url TEXT,
  rendered_image_url TEXT,
  qrcode_url TEXT,
  credits_consumed INTEGER,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME
);
```

## 五、后端调用示例

```python
import requests
import time

TRIPO_API_KEY = "your-api-key"
BASE = "https://openapi.tripo3d.ai/v3"

def generate_3d_model(image_url):
    resp = requests.post(
        f"{BASE}/generation/image-to-model",
        headers={
            "Authorization": f"Bearer {TRIPO_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "input": image_url,
            "model": "v3.1-20260211",
            "texture": True,
            "pbr": True,
            "texture_quality": "detailed",
            "enable_image_autofix": True,
            "orientation": "align_image",
            "face_limit": 50000
        }
    )
    return resp.json()["data"]  # {"task_id": "task_abc123"}

def poll_task(task_id, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        data = requests.get(
            f"{BASE}/task/{task_id}",
            headers={"Authorization": f"Bearer {TRIPO_API_KEY}"}
        ).json()["data"]
        if data["status"] == "success":
            return data
        if data["status"] == "failed":
            raise Exception(f"Task failed: {data}")
        time.sleep(5)
    raise TimeoutError()
```

## 六、开发优先级

1. **P0**（半天）：后端 API 封装 + 任务轮询
2. **P1**（1天）：前端上传界面 + 结果展示
3. **P2**（1天）：二维码生成 + 视频嵌入
4. **P3**（3-5天）：Three.js 服务端渲染 + Remotion 深度集成（需 GPU 服务器）

## 七、成本估算

- Image-to-3D 单次生成约 100 credits
- 展会 50 家参展商约 5000 credits
- 企业客户可联系 business@tripo3d.ai 获取批量折扣

## 八、注意事项

- 图片格式支持 PNG/JPEG/WebP，最大 20MB
- 推荐分辨率至少 256×256px
- 产品主体要清晰可见，背景干净，遮挡尽量少
- `enable_image_autofix` 可以自动优化低质量图片
- GLB 格式可直接在浏览器中用 `<model-viewer>` 组件展示
