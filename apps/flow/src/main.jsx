import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App.jsx";
import { LegacyBridge } from "./LegacyBridge.jsx";
createRoot(document.getElementById("root")).render(
 <React.StrictMode>
  {location.protocol==="file:"?<App />:<LegacyBridge />}
 </React.StrictMode>,
);
