# さくら上の PHP に近い構成で index.php を動かすためだけのイメージ。
# pdo_mysql はソースが同梱されているので、ネットワーク無しでビルドできる。
FROM php:8.2-cli
RUN docker-php-ext-install pdo_mysql
WORKDIR /var/www/html
