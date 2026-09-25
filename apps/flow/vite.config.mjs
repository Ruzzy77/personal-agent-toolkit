import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {workspacePlugin} from './server/workspace.mjs';
export default defineConfig({build:{outDir:'dist/client'},optimizeDeps:{include:['react','react-dom/client']},server:{host:'127.0.0.1',strictPort:true,watch:{ignored:['**/.data/**','**/.agent-runtime/**']},fs:{deny:['.env','.env.*','*.{crt,pem}','**/.git/**','**/.data/**','**/.agent-runtime/**']}},plugins:[react(),workspacePlugin({host:process.env.TOOLKIT_FLOW_EXEC_HOST,workspaceRoot:process.env.TOOLKIT_FLOW_WORKSPACE_ROOT})]});
