# Phigros Chart Tracker

自动检测 Phigros 游戏更新，解包铺面（谱面）并上传到仓库。

## ⚠️ 免责声明 / Disclaimer

**本仓库仅供学习和研究目的，所有铺面资源的版权归 Pigeon Games（鸽游）所有。**

1. 本仓库中的铺面数据是通过技术手段从游戏安装包中提取的，**未获得 Pigeon Games 的授权**。
2. 下载后请于 **24 小时内删除**。如果您喜欢 Phigros，请前往 [官方渠道](https://www.pigeon-games.cn/) 下载游戏并支持开发者。
3. 本仓库的创建者不对任何人因使用本仓库内容而造成的任何直接或间接损失负责。
4. 任何因使用本仓库内容而产生的版权纠纷，均与仓库创建者无关，由使用者自行承担全部责任。
5. 如果 Pigeon Games 认为本仓库侵犯了其权益，请联系仓库创建者，将在收到通知后立即删除相关内容。

**By using this repository, you agree that:**
- All chart resources are copyrighted by Pigeon Games.
- You must delete all downloaded content within **24 hours**.
- The repository creator is NOT responsible for any legal issues arising from your use of this content.
- If Pigeon Games requests removal, all content will be deleted immediately.

## 工作原理

1. **定时检测**：GitHub Actions 每 6 小时自动查询好游快爆 API 获取 Phigros 最新版本号
2. **版本比较**：与仓库中记录的版本对比，判断是否有新版本
3. **下载 APK**：使用好游快爆客户端 UA 下载最新安装包
4. **最小化解压**：只解压 `catalog.json` 和铺面相关的 bundle，节省磁盘空间
5. **增量解包**：只提取新增的铺面，不删除已有铺面
6. **自动提交**：将新铺面推送到仓库

## 目录结构

```
├── .github/workflows/check-update.yml  # GitHub Actions 工作流
├── phigros_unpacker/                   # 解包核心库
│   ├── __init__.py
│   ├── catalog.py                      # Addressables catalog 解析
│   ├── constants.py                    # 常量定义
│   ├── extractors.py                   # Unity 资源提取器
│   ├── pipeline.py                     # 解包流水线
│   └── utils.py                        # 工具函数
├── scripts/
│   └── unpack.py                       # 自动解包脚本
├── charts/                             # 解包输出的铺面（自动生成）
│   └── <songsId>/                      # 每首歌一个目录
│       ├── Chart_EZ.json
│       ├── Chart_HD.json
│       ├── Chart_IN.json
│       └── Chart_AT.json
├── version.json                        # 当前跟踪的版本信息
├── requirements.txt
└── README.md
```

## 铺面格式

铺面 JSON 文件位于 `charts/<songsId>/Chart_<难度>.json`，其中：

- `songsId` 是游戏内部标识，如 `Glaciaxion.SunsetRay.0`
- 难度包括：`EZ`、`HD`、`IN`、`AT`、`Legacy`（隐藏旧谱）、`SP`（特殊谱面）

## GitHub Actions 配置

### 定时执行

工作流每 6 小时自动检测一次更新（北京时间 8:00 / 14:00 / 20:00 / 2:00），也可手动触发。

### 增量更新

- 只上传新增的铺面，不删除已有铺面
- 通过扫描 `charts/` 目录已有的 `song_id/Chart_*.json` 与 catalog 对比来确定新增
- 版本号相同时直接跳过，不重复下载

### 手动触发

在 Actions 页面点击 "Run workflow"，可选开启 "强制更新" 模式（忽略版本检查，重新解包所有铺面）。

### 所需权限

工作流需要 `contents: write` 权限以自动提交铺面更新。如果你 fork 了此仓库，需要确保：

1. 仓库 **Settings → Actions → General → Workflow permissions** 设为 **"Read and write permissions"**
2. 或者使用 PAT token

## 技术细节

### 最小化 APK 解压

APK 约 2.4GB，但铺面 JSON 仅占很小一部分。脚本采用两阶段解压：

1. 先只解压 `catalog.json`，解析出铺面 bundle 列表
2. 再只解压铺面相关的 `.bundle` 文件

这避免了在 GitHub Actions 有限的磁盘空间中解压整个 APK。

### 解包流程

```
APK (zip)
  └─ assets/aa/catalog.json          → 解析 Addressables 资源索引
  └─ assets/aa/Android/*.bundle      → Unity AssetBundle
       └─ TextAsset (Chart_EZ.json)  → 输出铺面 JSON
```

### 下载可靠性

- 使用好游快爆客户端 UA：`Androidkb/1.5.8.007(android;PJX110;16;1080x2256;WiFi)`
- 支持断点续传（HTTP Range）
- MD5 校验确保文件完整性
- 最多 3 次重试

## 致谢

- Phigros 由 Pigeon Games（鸽游）开发
- 好游快爆提供 APK 分发渠道
