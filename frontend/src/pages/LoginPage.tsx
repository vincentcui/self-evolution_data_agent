/* ════════════════════════════════════════════
 *  登录页 — 左侧产品视觉 + 右侧登录表单
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Button, Form, Input, message } from "antd";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { login as apiLogin } from "@/api";
import styles from "./LoginPage.module.css";

const LoginPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const resp = await apiLogin(values);
      login(resp.access_token, resp.user);
      message.success("登录成功");
      navigate("/");
    } catch (err: any) {
      message.error(err.response?.data?.detail || "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.container}>
      <section className={styles.visualPane} aria-label="Data Agent product visual">
        <div className={styles.heroFrame}>
          <img
            className={styles.heroImage}
            src="/login-hero.png"
            alt="自演化知识驱动的多源数据库智能问数系统"
          />
        </div>
      </section>

      <section className={styles.formPane}>
        <div className={styles.loginBox}>
          <h1 className={styles.title}>欢迎登录</h1>
          <p className={styles.subtitle}>自演化知识驱动的多源数据库智能问数系统</p>

          <Form
            layout="vertical"
            onFinish={handleSubmit}
            className={styles.form}
          >
            <Form.Item
              name="username"
              rules={[{ required: true, message: "请输入用户名" }]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder="请输入账号"
                autoComplete="username"
                size="large"
              />
            </Form.Item>

            <Form.Item
              name="password"
              rules={[{ required: true, message: "请输入密码" }]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="请输入密码"
                autoComplete="current-password"
                size="large"
              />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" loading={loading} size="large" block>
                登录
              </Button>
            </Form.Item>
          </Form>
        </div>
      </section>
    </div>
  );
};

export default LoginPage;
