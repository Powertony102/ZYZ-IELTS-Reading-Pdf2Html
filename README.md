# 项目简介

将 IELTS 阅读类 PDF 批量转换为可离线练习的 HTML：左侧文章、右侧题目、底部导航、右下角计时器，提交后再显示答案与评分。页面状态（作答、笔记、高亮）全部保存在浏览器 localStorage 中，可随时恢复。

## 目录概览
- `Script/pdf_pipeline.py`：Python 主流程（步骤 A–G：类型判定、区块定位、段落拆分、题目解析、答案解析、校验、HTML 生成）。
- `Script/templates/base.html`：统一 HTML 模板（计时、导航、保存/重置、高亮、笔记、成绩展示）。
- `Petrol power an eco-revolution.html`：模板化后的示例页面。
- `ZYZ 1月高频（139篇+29背景）/`：用户数据，不纳入版本控制。

## 环境与依赖
- Python 3.9+。
- 需要自行安装 PDF 解析依赖（离线/内网环境请提前下载）：`pip install pdfplumber pypdf`。

## 使用方式
1) 预装依赖  
```bash
pip install pdfplumber pypdf
```
2) 批处理示例（单 PDF 先行测试）  
```bash
python Script/pdf_pipeline.py "ZYZ 1月高频（139篇+29背景）/P3（41高+9次）/1. 高频/187. P3 - Petrol power an eco-revolution 交通的革命【高】.pdf" \
  --output-dir "ZYZ 1月高频（139篇+29背景）/P3（41高+9次）/1. 高频" \
  --bundle-pdf \
  --limit 1
```
参数说明：
- `--output-dir`：输出根目录（默认与输入同级）。每个 PDF 会生成同名文件夹，内含 HTML；`--bundle-pdf` 会顺带复制源 PDF。
- `--report`：`report.csv` 路径，记录页数、字符数、扫描疑似、解析结果等。
- `--force-html`：即便校验失败（NEEDS_FIX）也生成 HTML，便于手动/Gemini 修复。
- `--limit`：调试时限制数量。

## 流水线要点（A–G）
- A：抽取前 1–2 页，计算字符数与 ASCII 比例，标记疑似扫描。
- B：按关键词页定位 Passage / Questions / Answer Key 的起止。
- C：段落拆分：有 A/B/C 标签则按标签，否则按空行生成 P1/P2…。
- D：题型识别：MCQ（含 A/B/C…选项）、TF/NG、YN/NG、填空等；保留 raw 文本便于人工或 Gemini 兜底。
- E：答案键解析：支持 “1 C / 14 ii / TRUE / YES / NG” 等格式。
- F：一致性校验：题量/答案量匹配、扫描提示；失败则标记 NEEDS_FIX。
- G：HTML 生成：沿用模板，计时器固定右下，提交后显示成绩/答案，localStorage 自动保存。

## HTML 模板特性
- 右下角计时器（暂停/恢复），底部题号导航。
- 自动保存：作答、笔记、高亮、成绩均落地 localStorage，`Save` 手动触发，`Reset` 双重确认。
- 高亮与笔记：文本选中后可高亮/移除，高亮同步保存；侧边笔记支持复制。
- 提交后显示评分与答案列表；未提交前不暴露答案。
- 简洁风格，无花哨元素，移动端自适应。

## Gemini 兜底建议
- `report.csv` 中状态为 `NEEDS_FIX` 的题目可用 `--force-html` 生成草稿，再将解析失败的块（questions/answers/raw）喂给 Gemini CLI 做结构化修复，修后回填 JSON 或直接改 HTML。

## 故障排除

### 生成的HTML页面空白
**原因**：缺少PDF处理库，或者之前版本有bug。

**解决方案**：
1. 确保已安装依赖：
   ```bash
   pip install pdfplumber pypdf
   ```

2. 删除旧的HTML文件并重新生成：
   ```bash
   python Script/pdf_pipeline.py --force-html --bundle-pdf "path/to/your.pdf"
   ```

3. 检查`report.csv`查看解析状态

### 题型识别错误
- 检查PDF格式是否标准
- 部分MCQ题目可能被误识别为TF/NG，这通常是因为选项文本中包含"no"/"yes"等关键词
- 使用`--force-html`生成后手动调整

### 答案缺失
- 部分PDF不包含答案页（如本示例的187题），这是正常的
- 生成的HTML仍可正常使用，只是不会显示参考答案
- 可以单独提供答案文件或手动添加到HTML中

## 注意事项
- 数据目录 `ZYZ 1月高频（139篇+29背景）/` 已在 `.gitignore`，避免把用户 PDF/答题数据推到仓库。
- 浏览器本地缓存：清理缓存会丢失作答/高亮/笔记；每个 HTML 使用独立 `STORAGE_KEY`。
- 复位按钮有二次确认，避免误触。

## 已知限制
- 当前环境未预装 PDF 解析库，需手动安装后再运行脚本。
```bash
conda install -c conda-forge pdfplumber pypdf
# 或者用 pip
pip install pdfplumber pypdf

```
- 题型识别为规则版：少量特殊版式可能落入 NEEDS_FIX；请结合 Gemini/人工校正。

