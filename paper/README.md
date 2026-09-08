# 论文材料目录

本目录集中保存 Journal of Agricultural Engineering 投稿相关材料，不包含历史渲染缓存。

## 目录结构

- `manuscript/`：期刊主稿和补充材料 DOCX。
- `figures/`：论文图件、实验指标 JSON 和迭代结果 CSV。
- `scripts/`：数据导出、实验复现、论文图件和 DOCX 生成脚本。
- `data/`：汇总导出的 XLSX 数据集。
- `notes/`：论文写作依据和项目创新点说明。

## 生成方式

在项目根目录执行：

```powershell
python paper/scripts/build_paper_docx.py
```

该命令会更新：

- `paper/manuscript/论文初稿_Journal_of_Agricultural_Engineering.docx`
- `paper/manuscript/论文补充材料_Journal_of_Agricultural_Engineering.docx`

重新生成实验结果和图件时，依次执行：

```powershell
python paper/scripts/run_ablation_experiment.py
python paper/scripts/run_controller_monte_carlo.py
python paper/scripts/run_iteration_experiments.py
python paper/scripts/build_paper_figures.py
python paper/scripts/build_paper_docx.py
```

重新导出汇总数据集：

```powershell
python paper/scripts/export_dataset_xlsx.py
```

主稿按照期刊限制保留 9 个表和 6 幅图；详细实验表格、附录和其余图片保存在补充材料中。
