# 28 · 导出项目时支持按 UI 目录树建文件结构

**工作量**：XS
**优先级**：P2
**状态**：待做

## 来源

导出项目时，`files/` 目录默认拍平（`<id>__<原文件名>`）。task #17 引入 `files.subfolder` 后，
导出时可以选择按 UI 文件树的逻辑目录结构组织导出文件，让导出包直接可用、结构可读。

## 目标

导出对话框新增「文件目录结构」选项，让用户选择导出文件的组织方式。

## 前置依赖

**强依赖** task #17 T1（`files.subfolder` 字段）。若 #17 未完成，该选项灰显默认"拍平"。

## 实现要点

### A. 导出对话框新增选项

```
文件目录结构：
◉ 保留项目内目录结构（按 UI 文件树建子目录）
○ 拍平到 files/（所有文件平铺，用 id 前缀防冲突）
```

- 默认选"保留目录结构"
- 若当前库 schema 版本 < 7（无 `subfolder` 列），选项灰显并默认"拍平"

### B. `ExportOptions` 新增字段

```python
preserve_structure: bool = True
```

### C. `export_project` 逻辑分支

**preserve_structure = True**：
- 按 `file.subfolder` 建子目录，文件用原名存放
- 冲突处理：同 subfolder 下重名仍加 `_1` 序号

```
files/
├── sub/
│   └── x.pdf           ← subfolder="sub"
├── y.pdf               ← subfolder=""
└── deep/
    └── nested/
        └── z.pdf       ← subfolder="deep/nested"
```

**preserve_structure = False**：
- 原方案：所有文件平铺，`<id>__` 前缀防冲突

```
files/
├── 1__x.pdf
├── 2__y.pdf
└── 3__z.pdf
```

### D. `files.json` 新增字段

```json
{
  "preserve_structure": true,
  "files": [
    {
      "id": 1,
      "subfolder": "sub",
      "exported_to": "files/sub/foo.pdf",
      ...
    }
  ]
}
```

## 校验

- [ ] 选"保留目录结构" + 项目有 subfolder → `files/` 下出现子目录，文件用原名
- [ ] 选"拍平" → `files/` 下所有文件平铺，带 `<id>__` 前缀
- [ ] subfolder 为空的文件始终落在 `files/` 顶层
- [ ] #17 未完成时选项灰显，导出走拍平逻辑

## 依赖

- **强依赖** task #17 T1（`files.subfolder`）
- 被 task #25（批量导出）复用：批量导出时同一选项对所有项目统一生效
