/* ════════════════════════════════════════════
 *  WorkspacePage — 工作台
 *  显示管理功能入口卡片
 * ════════════════════════════════════════════ */

import React from "react";
import { useNavigate } from "react-router-dom";
import { Card, Row, Col, Typography } from "antd";
import {
  BarChartOutlined,
  DatabaseOutlined,
  RobotOutlined,
  BookOutlined,
  SettingOutlined,
  ExperimentOutlined,
  UserOutlined,
  ShareAltOutlined,
} from "@ant-design/icons";

const { Title } = Typography;

const workspaceItems = [
  { path: "/", icon: <BarChartOutlined />, label: "智能查询", desc: "自然语言问数" },
  { path: "/namespaces", icon: <DatabaseOutlined />, label: "命名空间", desc: "管理空间与数据源" },
  { path: "/model-management", icon: <RobotOutlined />, label: "模型管理", desc: "API Key 配置" },
  { path: "/knowledge", icon: <BookOutlined />, label: "知识库", desc: "业务术语与口径" },
  { path: "/profiles", icon: <SettingOutlined />, label: "Profile 管理", desc: "提取器配置" },
  { path: "/admin/agent-traces", icon: <ExperimentOutlined />, label: "Trace 提炼", desc: "从执行过程沉淀知识" },
  { path: "/users", icon: <UserOutlined />, label: "用户管理", desc: "成员与权限" },
  { path: "/shares", icon: <ShareAltOutlined />, label: "分享管理", desc: "管理分享链接" },
];

const WorkspacePage: React.FC = () => {
  const navigate = useNavigate();

  return (
    <div style={{ padding: 32, maxWidth: 960, margin: "0 auto" }}>
      <Title level={3} style={{ marginBottom: 24 }}>工作台</Title>
      <Row gutter={[16, 16]}>
        {workspaceItems.map((item) => (
          <Col key={item.path} xs={24} sm={12} md={8} lg={6}>
            <Card
              hoverable
              onClick={() => navigate(item.path)}
              style={{ textAlign: "center" }}
            >
              <div style={{ fontSize: 32, marginBottom: 8, color: "#1677ff" }}>
                {item.icon}
              </div>
              <Card.Meta title={item.label} description={item.desc} />
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
};

export default WorkspacePage;
