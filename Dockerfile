# 基于现有 zoogent 镜像，固化 lark-cli 版本
# 用法：docker build -t zoogent:latest .
# 升级 lark-cli 时改 ARG 即可：docker build --build-arg LARK_CLI_VERSION=1.0.89 -t zoogent:latest .
FROM zoogent:latest

ARG LARK_CLI_VERSION=1.0.88

RUN npm i -g @larksuite/cli@${LARK_CLI_VERSION} \
    && lark-cli --version
