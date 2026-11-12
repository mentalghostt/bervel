# Интерфейс BERVEL

React и TypeScript, сборка Vite. Вход приложения — src/main.tsx, разделы и hash-маршруты — src/App.tsx. Радар работает с постоянными профилями через radarProfilesApi.ts; пресейл, файлы, ассистент, проверка и обращения используют свои API-клиенты.

Из этой папки: npm ci, npm run dev, npm run build, npm run lint. Перед dev-запуском серверу нужны BERVEL_WEB_ACCESS_KEY и адрес API в BERVEL_API_TARGET. Ключ остаётся на сервере и не передаётся браузеру. predev и prebuild копируют необходимые ресурсы PDF.js в public/pdfjs.

Проверки интерфейса находятся в .dev/tests, команды — в .dev/package.json. Из корня npm run test:frontend --prefix .dev запускает проверки с подменённым API; BERVEL_FRONTEND_URL задаёт адрес проверяемого интерфейса. BERVEL_BROWSER_PATH или BERVEL_BROWSER_CHANNEL позволяют выбрать установленный браузер; по умолчанию используется Chromium Playwright. Проверки с реальным API запускаются отдельно.

Сборка исходников не устанавливает их в работающие контейнеры. Порядок локального запуска и выпуска описан в .dev/docs/operations/docker-local.md.
