import React from "react";
import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { roleAtLeast } from "@/utils/role";

/** 仅 super_admin 可进的路由守卫；其余角色重定向回首页。 */
const RequireSuperAdmin: React.FC = () => {
  const { user, loading } = useAuth();
  if (loading) return null;
  return roleAtLeast(user?.role, "super_admin") ? <Outlet /> : <Navigate to="/" replace />;
};

export default RequireSuperAdmin;
