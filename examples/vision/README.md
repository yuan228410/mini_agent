# Vision 子代理使用示例

Vision 子代理用于图片分析、OCR、图表识别等视觉任务。

## 基本用法

```
用户: 分析这张图片 https://example.com/image.png
```

模型自动派遣 vision 子代理：
```python
dispatch_subagent(type="vision", task="分析这张图片 https://example.com/image.png")
```

---

## 本地图片

```
用户: 分析本地图片 /Users/yuanzhixiang/Downloads/screenshot.png
```

模型自动派遣：
```python
dispatch_subagent(type="vision", task="分析 /Users/yuanzhixiang/Downloads/screenshot.png")
```

---

## 多图分析

```
用户: 对比这两张图片的差异：
     https://example.com/before.png
     https://example.com/after.png
```

模型自动派遣 vision 分析多张图片。

---

## OCR 提取文字

```
用户: 提取这张图片中的文字
     https://example.com/document.png
```

Vision 子代理会识别并提取图片中的文本内容。

---

## 自动图片处理

**重要改进：无需手动下载图片！**

Vision 子代理会自动：
1. 检测 task 中的图片 URL
2. 下载到临时目录
3. 压缩大图（例如 5.7MB → 62KB）
4. 转换为 base64
5. 派遣 vision 分析
6. 清理临时文件

**正确示例：**
```
用户: 分析这张图片 https://example.com/image.png

模型: dispatch_subagent(type="vision", task="分析这张图片 https://...")
```

**错误示例（不要这样做）：**
```
# ❌ 不需要手动下载
run_command("curl -o /tmp/xxx.png https://...")
dispatch_subagent(type="vision", task="分析 /tmp/xxx.png")
```

---

## 触发关键词

- "分析图片"
- "识别图片"
- "看下这张图"
- "OCR"
- "提取图片文字"
- "图片里有什么"

---

## 支持的图片格式

- PNG
- JPEG / JPG
- GIF
- WebP
- BMP

---

## 配置

Vision 子代理使用配置中的视觉模型（如 Claude 3.5 Sonnet），无需主 Agent 具备视觉能力。

在 `~/.mini_ai/config.yaml` 中配置：
```yaml
models:
  vision:
    name: claude
    model: claude-3-5-sonnet-20241022
```
