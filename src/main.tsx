import { createRoot } from "react-dom/client";
import App from "./app/App.tsx";
import "./styles/index.css";

// 从 URL ?token= 读取 token 并持久化到 localStorage
(function syncTokenFromUrl() {
  try {
    const params = new URLSearchParams(window.location.search);
    const t = params.get("token");
    if (t && t.trim()) {
      localStorage.setItem("overleaf_access_token", t.trim());
    }
  } catch (_) {
    // ignore
  }
})();

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element not found");
}

const root = createRoot(rootElement);
root.render(<App />);