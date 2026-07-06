/* ════════════════════════════════════════════
 *  App 路由入口 — 路由守卫 + 嵌套布局
 * ════════════════════════════════════════════ */

import React from "react";
import { Routes, Route, Navigate, Outlet } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Layout from "./components/Layout";
import QueryPageWrapper from "./components/QueryPageWrapper";
import LoginPage from "./pages/LoginPage";
import ShareViewPage from "./pages/ShareViewPage";
import ProfilePage from "./pages/ProfilePage";
import NamespacePage from "./pages/NamespacePage";
import ModelManagement from "./pages/ModelManagement";
import KnowledgePage from "./pages/KnowledgePage";
import ProfileManagement from "./pages/ProfileManagement";
import AgentTracesPage from "./pages/AgentTracesPage";
import UserManagePage from "./pages/UserManagePage";
import ShareManagePage from "./pages/ShareManagePage";
import { roleAtLeast } from "@/utils/role";

/* ── 认证守卫 ── */
const RequireAuth: React.FC = () => {
  const { token, loading } = useAuth();
  if (loading) return null;
  return token ? <Outlet /> : <Navigate to="/login" replace />;
};

const App: React.FC = () => (
  <AuthProvider>
    <Routes>
      {/* 公开路由 */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/share/:token" element={<ShareViewPage />} />

      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/" element={<QueryPageWrapper />} />
          <Route path="/profile" element={<ProfilePage />} />
          {/* 管理员页面 — readiness 链接跳转目标 */}
          <Route path="/namespaces" element={<NamespacePage />} />
          <Route path="/namespaces/:nsId" element={<NamespacePage />} />
          <Route path="/model-management" element={<ModelManagement />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/profiles" element={<ProfileManagement />} />
          <Route path="/admin/agent-traces" element={<AgentTracesPage />} />
          <Route path="/users" element={<UserManagePage />} />
          <Route path="/shares" element={<ShareManagePage />} />
        </Route>
      </Route>
    </Routes>
  </AuthProvider>
);

export default App;
