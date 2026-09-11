# GitHub 发布检查清单

## 1. 在本机确认身份

以下配置只作用于当前仓库：

```powershell
git config user.name "Aris Xu"
git config user.email "acexbr@outlook.com"
git config --local --list
```

## 2. 检查敏感信息

不要提交 `.env`、API Key、位置历史或内部业务数据。提交前执行：

```powershell
git status
git diff --cached
git grep -n -I -E "(API_KEY|TOKEN|PASSWORD|sk-)"
```

示例变量名可能被检出，但等号右侧必须为空或为明确的演示占位值。

## 3. 首次初始化并提交

仅当此目录尚未存在 `.git` 时执行 `git init`：

```powershell
git init
git branch -M main
git add .
git commit -m "feat: initialize urban mobility RAG agent"
```

然后在 GitHub 新建一个空仓库，不要勾选自动创建 README、LICENSE 或 `.gitignore`。将下面地址替换成真实仓库地址：

```powershell
git remote add origin https://github.com/<your-account>/urban-mobility-rag-agent.git
git remote -v
git push -u origin main
```

如果已经存在 `origin`，使用 `git remote set-url origin <真实地址>`，不要重复执行 `git remote add origin`。

## 4. 上传后核对

- GitHub 首页正确显示 README，而不是密钥或本地路径。
- Commits 页面作者显示为 Aris Xu；邮箱是否公开取决于 GitHub 邮箱隐私设置。
- Actions 中 CI 通过。
- `data/index/`、`.env`、`.venv/` 和 `__pycache__/` 没有出现在仓库中。
- 简历只写已经亲自运行、能够解释和复现的功能与指标。
