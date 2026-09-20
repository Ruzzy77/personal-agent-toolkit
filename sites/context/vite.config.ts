import { existsSync } from "node:fs";
import tailwindcss from "@tailwindcss/postcss";
import vinext from "vinext";
import { defineConfig } from "vite";
export default defineConfig(async()=>{
 process.env.WRANGLER_WRITE_LOGS ??= "false";
 process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
 process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";
 const {cloudflare}=await import("@cloudflare/vite-plugin");
 const configPath=existsSync(new URL("./wrangler.jsonc",import.meta.url))?"wrangler.jsonc":"wrangler.example.jsonc";
 return {css:{postcss:{plugins:[tailwindcss()]}},plugins:[vinext(),cloudflare({viteEnvironment:{name:"rsc",childEnvironments:["ssr"]},configPath})]};
});
