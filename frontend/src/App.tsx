/* ════════════════════════════════════════════
 *  App 路由入口 — 路由守卫 + 嵌套布局
 * ════════════════════════════════════════════ */

import React from "react";
import { Routes, Route, Navigate, Outlet } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Layout from "./components/Layout";
import WorkspaceLayout from "./components/WorkspaceLayout";
import QueryPage from "./pages/QueryPage";
import NamespacePage from "./pages/NamespacePage";
import KnowledgePage from "./pages/KnowledgePage";
import UserManagePage from "./pages/UserManagePage";
import ShareManagePage from "./pages/ShareManagePage";
import LoginPage from "./pages/LoginPage";
import ShareViewPage from "./pages/ShareViewPage";
import AgentTracesPage from "./pages/AgentTracesPage";
import ProfilePage from "./pages/ProfilePage";
import ProfileManagement from "./pages/ProfileManagement";
import ModelManagement from "./pages/ModelManagement";
import WorkspacePage from "./pages/WorkspacePage";
import { roleAtLeast } from "@/utils/role";

/* ── 认证守卫 ── */
const RequireAuth: React.FC = () => {
  const { token, loading } = useAuth();
  if (loading) return null;
  return token ? <Outlet /> : <Navigate to="/login" replace />;
};

/* ── 管理准入守卫 ── */
const RequireAdmin: React.FC = () => {
  const { user } = useAuth();
  return roleAtLeast(user?.role, "admin") ? <Outlet /> : <Navigate to="/" replace />;
};

const App: React.FC = () => (
  <AuthProvider>
    <Routes>
      {/* 公开路由 */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/share/:token" element={<ShareViewPage />} />

      <Route element={<RequireAuth />}>
        {/* 主页布局 — 侧边栏只有会话 + 工作台 */}
        <Route element={<Layout />}>
          <Route path="/" element={<QueryPage />} />
          <Route path="/profile" element={<ProfilePage />} />
        </Route>

        {/* 工作台布局 — 左侧导航 + 右侧内容，和原来首页一样 */}
        <Route element={<WorkspaceLayout />}>
          <Route element={<RequireAdmin />}>
            <Route path="/workspace" element={<WorkspacePage />} />
            <Route path="/namespaces" element={<NamespacePage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/profiles" element={<ProfileManagement />} />
            <Route path="/model-management" element={<ModelManagement />} />
            <Route path="/users" element={<UserManagePage />} />
            <Route path="/shares" element={<ShareManagePage />} />
            <Route path="/admin/agent-traces" element={<AgentTracesPage />} />
          </Route>
        </Route>
      </Route>
    </Routes>
  </AuthProvider>
);

export default App;
