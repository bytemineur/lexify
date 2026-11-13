## 这个 PR 做什么

<!-- 一句话。如果是修 bug，说清是什么 bug -->

## 为什么

<!-- 动机比改动更重要。关联 issue：Closes #123 -->

## 怎么验证的

<!-- 贴命令和输出。改了采集源的，附上单源采集结果 -->

```bash
python tests/test_static.py
```

## 检查清单

- [ ] `python tests/test_static.py` 全绿
- [ ] 没有提交 `data/lexify.db`、采集缓存或 `.env`
- [ ] 采集相关改动附了验证输出
- [ ] 文档（README / docs/ / CHANGELOG）已同步
- [ ] 一个 PR 只做了一件事
