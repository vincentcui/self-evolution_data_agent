/* ════════════════════════════════════════════
 *  WorkspaceModal — 设置浮窗，毛玻璃背景
 * ════════════════════════════════════════════ */

import React from "react";
import { useNavigate } from "react-router-dom";
import { Card, Row, Col, Modal, Typography } from "antd";
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

interface Props {
  open: boolean;
  onClose: () => void;
}

const WorkspaceModal: React.FC<Props> = ({ open, onClose }) => {
  const navigate = useNavigate();

  const handleClick = (path: string) => {
    navigate(path);
    onClose();
  };

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      width={720}
      title={null}
      closable={false}
      styles={{
        body: { padding: 32 },
        mask: {
          backdropFilter: "blur(8px)",
          WebkitBackdropFilter: "blur(8px)",
          background: "rgba(0, 0, 0, 0.15)",
        },
      }}
      style={{ top: 40 }}
    >
      <Title level={4} style={{ marginBottom: 24 }}>工作台</Title>
      <Row gutter={[12, 12]}>
        {workspaceItems.map((item) => (
          <Col key={item.path} xs={12} sm={8} md={6}>
            <Card
              hoverable
              size="small"
              onClick={() => handleClick(item.path)}
              style={{ textAlign: "center" }}
            >
              <div style={{ fontSize: 28, marginBottom: 4, color: "#1677ff" }}>
                {item.icon}
              </div>
              <Card.Meta
                title={<span style={{ fontSize: 13 }}>{item.label}</span>}
                description={<span style={{ fontSize: 11 }}>{item.desc}</span>}
              />
            </Card>
          </Col>
        ))}
      </Row>
    </Modal>
  );
};

export default WorkspaceModal;
