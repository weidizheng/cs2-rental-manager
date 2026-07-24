# GitHub 与私有数据同步

本仓库只保存代码。`private-data/` 保存库存、SQLite、API 配置、RSA 私钥、日志和图片缓存，已被 Git 忽略。

## 首次上传

```powershell
git add .
git commit -m "Initial private repository setup"
git remote add origin https://github.com/weidizheng/cs2-rental-manager.git
git push -u origin main
```

首次 `git push` 会由 Git Credential Manager 打开浏览器完成 GitHub 登录授权。仓库必须保持 Private。

## 日常同步代码

```powershell
git add .
git commit -m "Describe the change"
git push
```

## 在另一台电脑恢复

```powershell
git clone https://github.com/weidizheng/cs2-rental-manager.git
cd cs2-rental-manager
py -3.13 -m pip install -r requirements.txt
```

随后从你的私有云盘复制整个 `private-data/` 到项目根目录。启动 `main.py` 即可继续使用。

也可以设置 `CS2_RENTAL_DATA_DIR`，让程序直接使用云盘上的私有数据目录；此目录不应位于仓库内，也绝不能提交到 Git。
