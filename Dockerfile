# 使用nginx作为Web服务器
FROM nginx:alpine

# 设置工作目录
WORKDIR /usr/share/nginx/html

# 删除nginx默认页面
RUN rm -rf /usr/share/nginx/html/*

# 复制项目文件到nginx目录
COPY index.html .
COPY courses.html .
COPY plan.html .
COPY resources.html .
COPY profile.html .
COPY about.html .
COPY contact.html .
COPY demo.html .
COPY css/ ./css/
COPY js/ ./js/

# 复制自定义nginx配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 暴露80端口
EXPOSE 80

# 启动nginx
CMD ["nginx", "-g", "daemon off;"]
