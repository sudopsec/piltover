FROM node:18-alpine

RUN apk add --no-cache python3 make g++ gcc linux-headers

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY . .

EXPOSE 3200
EXPOSE 10000-10100/udp

CMD ["node", "server.js"]