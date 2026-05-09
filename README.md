# 标准文件抽取与双模型复核项目

本项目采用两段式运行：

1. `paddleocr3` Docker 容器负责 GPU PaddleOCR 3.x 识别，生成 OCR 中间结果。
2. Windows Pytorch 环境负责模型抽取、RAG、双模型比对、人工复核和 Excel 输出。

## 环境

Pytorch 后处理环境：

```powershell
C:\ProgramData\anaconda3\envs\Pytorch\python.exe
```

OCR 容器：

```powershell
docker start -ai paddleocr3
```

## 配置

主要配置都在 `config.yaml`：

- 输入文件：`input.file_path`
- 批量目录：`input.input_dir`
- 输出目录：`input.output_dir`
- 单模型/双模型：`runtime.mode`
- 模型 A/B 名称与 API key：`models.model_a`、`models.model_b`
- OCR 开关：`ocr`
- RAG 阈值：`rag.similarity_threshold`

API key 直接写在 `config.yaml`：

```yaml
models:
  model_a:
    api_key: "sk-xxxx"
  model_b:
    api_key: "sk-xxxx"
```

## 一键联动运行

在 Windows PowerShell 中进入项目目录：

```powershell
cd E:\商网\ocr\国标文件提取
```

处理配置里的默认文件：

```powershell
.\run_linked_pipeline.bat
```

处理任意单个文件：

```powershell
.\run_linked_pipeline.bat --input "国内标准/某个文件.pdf"
```

批量处理目录：

```powershell
.\run_linked_pipeline.bat --input-dir "国内标准"
```

指定输出目录：

```powershell
.\run_linked_pipeline.bat --input-dir "国内标准" --output-dir "output_batch"
```

运行过程会先在 `paddleocr3` 容器中生成：

```text
output/ocr_manifest.json
output/<文件名>/raw_document.json
output/<文件名>/ocr_all_text.txt
```

然后 Windows Pytorch 环境会读取 `ocr_manifest.json`，继续生成：

```text
output/<文件名>/chunks.json
output/<文件名>/model_outputs.json
output/<文件名>/comparison_result.xlsx
output/<文件名>/final_review_result.xlsx
```

## 只看 OCR 中间结果

如果只想在容器中查看 OCR 结果，可进入容器后运行：

```bash
cd /workspace/standard_extraction
python scripts/ocr_only.py --config config.yaml --input "国内标准/GBT+3191-2019.pdf" --output-dir output
```

查看纯文本：

```bash
cat output/GBT+3191-2019/ocr_all_text.txt
```

查看结构化 OCR：

```bash
cat output/GBT+3191-2019/raw_document.json
```

## Prompts

`prompts/prompts.yaml` 中维护字段分组提示词。目前 `G-1-General` 用于：

- 规范号
- 规范版本
- 规范年代
- 规范名称
- 材料类型
- 应用场景

## 打包

如果 Pytorch 环境安装了 PyInstaller，可运行：

```powershell
.\scripts\build_exe.ps1
```
# GB
