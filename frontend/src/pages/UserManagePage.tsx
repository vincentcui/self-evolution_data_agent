/* ════════════════════════════════════════════
 *  用户管理页面
 *  左右分栏: 用户列表 + 详情表单 (角色/激活/命名空间权限)
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Switch,
  Tag,
  message,
} from "antd";
import { PlusOutlined, UserOutlined } from "@ant-design/icons";
import * as api from "@/api";
import { useAuth } from "@/context/AuthContext";
import type { Namespace, User } from "@/types";
import styles from "@/styles/user.module.css";
import globalStyles from "@/styles/global.module.css";

/** 与后端 IS_PASSWORD_MIN_LENGTH 默认值同步 */
const PASSWORD_MIN_LENGTH = 8;

const UserManagePage: React.FC = () => {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [activeUserId, setActiveUserId] = useState<number | null>(null);
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [userAccess, setUserAccess] = useState<number[]>([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showResetModal, setShowResetModal] = useState(false);
  const [form] = Form.useForm();
  const [createForm] = Form.useForm();
  const [resetForm] = Form.useForm();
  const [loading, setLoading] = useState(false);

  /* 角色选项 — super_admin 三选, admin 仅 user (锁定) */
  const roleOptions =
    currentUser?.role === "super_admin"
      ? [
          { value: "super_admin", label: "超级管理员 (super_admin)" },
          { value: "admin", label: "管理员 (admin)" },
          { value: "user", label: "普通用户 (user)" },
        ]
      : [{ value: "user", label: "普通用户 (user)" }];

  /* 获取当前用户 ID (从 localStorage) */
  const currentUserId = (() => {
    try {
      const user = localStorage.getItem("user");
      return user ? JSON.parse(user).id : null;
    } catch {
      return null;
    }
  })();

  /* ── 初始化加载 ── */
  const loadUsers = useCallback(async () => {
    const list = await api.fetchUsers();
    setUsers(list);
    /* 自动选中第一个用户 (仅首次加载时) */
    if (list.length > 0 && activeUserId === null) {
      setActiveUserId(list[0].id);
    }
    return list;  // 返回加载的列表，供调用方直接使用
  }, [activeUserId]);

  const loadNamespaces = useCallback(async () => {
    const list = await api.fetchNamespaces();
    setNamespaces(list);
  }, []);

  useEffect(() => {
    loadUsers();
    loadNamespaces();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);  // 仅在组件挂载时执行一次

  /* ── 选中用户时加载权限 + 更新表单 ── */
  useEffect(() => {
    if (!activeUserId) return;

    const loadUserData = async () => {
      const user = users.find((u) => u.id === activeUserId);
      if (!user) return;

      /* 加载命名空间权限 */
      const accessList = await api.getUserAccess(activeUserId);
      const nsIds = accessList.map((ns) => ns.id);
      setUserAccess(nsIds);

      /* 更新表单值 */
      form.setFieldsValue({
        role: user.role,
        is_active: user.is_active,
        namespace_ids: nsIds,
      });
    };

    loadUserData();
  }, [activeUserId, users, form]);

  /* ── 创建用户 ── */
  const handleCreate = async () => {
    try {
      const vals = await createForm.validateFields();
      await api.createUser(vals);
      message.success("用户创建成功");
      setShowCreateModal(false);
      createForm.resetFields();
      /* 重新加载用户列表，从返回值中查找新用户 (避免双重 API 调用) */
      const updatedUsers = await loadUsers();
      const newUser = updatedUsers.find((u) => u.username === vals.username);
      if (newUser) setActiveUserId(newUser.id);
    } catch (err: any) {
      if (err.response?.status === 409) {
        message.error("用户名已存在");
      } else {
        message.error("创建失败");
      }
    }
  };

  /* ── 保存用户信息 ── */
  const handleSave = async () => {
    if (!activeUserId) return;
    setLoading(true);
    try {
      const vals = await form.validateFields();
      /* 更新角色和激活状态 */
      await api.updateUser(activeUserId, {
        role: vals.role,
        is_active: vals.is_active,
      });
      /* 更新命名空间权限 */
      await api.setUserAccess(activeUserId, vals.namespace_ids || []);
      message.success("保存成功");
      await loadUsers();
    } catch {
      message.error("保存失败");
    } finally {
      setLoading(false);
    }
  };

  /* ── 删除用户 ── */
  const handleDelete = async () => {
    if (!activeUserId) return;
    try {
      await api.deleteUser(activeUserId);
      message.success("用户已删除");
      /* 重新加载用户列表，从返回值中选中第一个用户 (避免双重 API 调用) */
      const updatedUsers = await loadUsers();
      if (updatedUsers.length > 0) {
        setActiveUserId(updatedUsers[0].id);
      } else {
        setActiveUserId(null);
      }
    } catch {
      message.error("删除失败");
    }
  };

  const activeUser = users.find((u) => u.id === activeUserId);
  const isSelf = activeUserId === currentUserId;

  /* ── 重置密码 (上级特权) ── */
  const handleReset = async () => {
    if (!activeUserId) return;
    try {
      const vals = await resetForm.validateFields();
      await api.resetUserPassword(activeUserId, vals.new_password);
      message.success("密码已重置");
      setShowResetModal(false);
      resetForm.resetFields();
    } catch (err: any) {
      if (err?.errorFields) return;
      message.error(err.response?.data?.detail || "重置失败");
    }
  };

  return (
    <div>
      {/* ── 页面头部 ── */}
      <div className={globalStyles.pageHeader}>
        <div>
          <h1 className={globalStyles.pageTitle}>用户管理</h1>
          <p className={globalStyles.pageSubtitle}>
            管理用户账号、角色和命名空间访问权限
          </p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setShowCreateModal(true)}
        >
          创建用户
        </Button>
      </div>

      {/* ── 左右分栏 ── */}
      <div className={styles.container}>
        {/* 左侧: 用户列表 */}
        <div className={styles.nsList}>
          {users.map((user) => (
            <div
              key={user.id}
              className={
                activeUserId === user.id ? styles.nsCardActive : styles.nsCard
              }
              onClick={() => setActiveUserId(user.id)}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <UserOutlined style={{ fontSize: 14, color: "#2563eb" }} />
                <div className={styles.nsCardName}>{user.username}</div>
              </div>
              <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                <Tag
                  color={
                    { super_admin: "gold", admin: "blue", user: "default" }[user.role]
                  }
                  style={{ fontSize: 10, margin: 0 }}
                >
                  {user.role}
                </Tag>
                {!user.is_active && (
                  <Tag color="error" style={{ fontSize: 10, margin: 0 }}>
                    已禁用
                  </Tag>
                )}
              </div>
              <div className={styles.nsCardSlug}>
                创建于 {user.created_at?.slice(0, 10)}
              </div>
            </div>
          ))}
        </div>

        {/* 右侧: 用户详情表单 */}
        {activeUser ? (
          <div className={styles.detailPanel}>
            <div className={styles.detailHeader}>
              <div>
                <div className={styles.detailTitle}>{activeUser.username}</div>
                <div className={styles.detailMeta}>
                  ID: {activeUser.id} · 创建于 {activeUser.created_at?.slice(0, 10)}
                </div>
              </div>
            </div>

            <Form
              form={form}
              layout="vertical"
              style={{
                background: "white",
                border: "1px solid #e0e7ff",
                borderRadius: 12,
                padding: 20,
                marginBottom: 16,
              }}
            >
              {/* 用户名 (只读) */}
              <Form.Item label="用户名">
                <div style={{ fontSize: 14, color: "#1e3a5f", fontWeight: 500 }}>
                  {activeUser.username}
                </div>
              </Form.Item>

              {/* 角色 */}
              <Form.Item
                name="role"
                label="角色"
                rules={[{ required: true, message: "请选择角色" }]}
              >
                <Select
                  options={roleOptions}
                  style={{ width: 200 }}
                />
              </Form.Item>

              {/* 激活状态 */}
              <Form.Item
                name="is_active"
                label="账号状态"
                valuePropName="checked"
              >
                <Switch
                  checkedChildren="激活"
                  unCheckedChildren="禁用"
                />
              </Form.Item>

              {/* 命名空间权限 */}
              <Form.Item
                name="namespace_ids"
                label="命名空间访问权限"
                extra="勾选仅授予非创建者访问权; 标记「创建者」的空间属用户自有, 权限不可在此移除。"
              >
                <Checkbox.Group
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                  }}
                >
                  {namespaces.map((ns) => {
                    const isOwner = ns.created_by != null && ns.created_by === activeUserId;
                    return (
                      <Checkbox key={ns.id} value={ns.id} disabled={isOwner}>
                        <span style={{ fontWeight: 500 }}>{ns.name}</span>
                        <span style={{ color: "#64748b", fontSize: 12, marginLeft: 6 }}>
                          ({ns.slug})
                        </span>
                        {isOwner && (
                          <Tag color="gold" style={{ marginLeft: 8, marginRight: 0 }}>
                            创建者
                          </Tag>
                        )}
                      </Checkbox>
                    );
                  })}
                </Checkbox.Group>
              </Form.Item>

              {/* 保存按钮 */}
              <Form.Item style={{ marginBottom: 0 }}>
                <Button
                  type="primary"
                  onClick={handleSave}
                  loading={loading}
                >
                  保存
                </Button>
              </Form.Item>
            </Form>

            {/* 删除按钮 */}
            <Button
              onClick={() => setShowResetModal(true)}
              style={{ marginRight: 8 }}
            >
              重置密码
            </Button>
            <Popconfirm
              title="确认删除用户?"
              description="此操作不可恢复"
              onConfirm={handleDelete}
              disabled={isSelf}
            >
              <Button
                danger
                disabled={isSelf}
                title={isSelf ? "不能删除自己" : ""}
              >
                删除用户
              </Button>
            </Popconfirm>
          </div>
        ) : (
          <div className={styles.empty}>
            ← 选择一个用户查看详情
          </div>
        )}
      </div>

      {/* ── 创建用户 Modal ── */}
      <Modal
        title="创建用户"
        open={showCreateModal}
        onOk={handleCreate}
        onCancel={() => {
          setShowCreateModal(false);
          createForm.resetFields();
        }}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="username"
            label="用户名"
            rules={[
              { required: true, message: "请输入用户名" },
              { pattern: /^[a-zA-Z0-9_-]+$/, message: "只能包含字母、数字、下划线和连字符" },
            ]}
          >
            <Input placeholder="如: zhangsan" />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            rules={[
              { required: true, message: "请输入密码" },
              { min: PASSWORD_MIN_LENGTH, message: `密码至少 ${PASSWORD_MIN_LENGTH} 位` },
              { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: "密码必须包含字母和数字" },
            ]}
          >
            <Input.Password placeholder="至少 8 位, 字母+数字" />
          </Form.Item>

          <Form.Item
            name="role"
            label="角色"
            initialValue="user"
            rules={[{ required: true, message: "请选择角色" }]}
          >
            <Select options={roleOptions} />
          </Form.Item>
        </Form>
      </Modal>

      {/* ── 重置密码 Modal ── */}
      <Modal
        title="重置密码"
        open={showResetModal}
        onOk={handleReset}
        onCancel={() => {
          setShowResetModal(false);
          resetForm.resetFields();
        }}
        okText="重置"
        cancelText="取消"
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: PASSWORD_MIN_LENGTH, message: `密码至少 ${PASSWORD_MIN_LENGTH} 位` },
              { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: "密码必须包含字母和数字" },
            ]}
          >
            <Input.Password placeholder="至少 8 位, 字母+数字" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default UserManagePage;
