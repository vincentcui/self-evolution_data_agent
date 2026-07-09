import React, { useState, useEffect } from "react";
import { Table, Button, Modal, Form, Input, Tag, Popconfirm, message, Space } from "antd";
import { PlusOutlined, KeyOutlined } from "@ant-design/icons";
import {
  fetchGitTokenConfigs, addGitTokenConfig, updateGitTokenConfig,
  deleteGitTokenConfig, activateGitTokenConfig, testGitTokenConfig,
} from "@/api";
import type { GitTokenConfig } from "@/types";

const GitTokenManagement: React.FC = () => {
  const [configs, setConfigs] = useState<GitTokenConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<GitTokenConfig | null>(null);
  const [form] = Form.useForm();

  // 测试可达性状态
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testModalOpen, setTestModalOpen] = useState(false);
  const [testingRecord, setTestingRecord] = useState<GitTokenConfig | null>(null);
  const [testUrl, setTestUrl] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchGitTokenConfigs();
      setConfigs(data);
    } catch {
      message.error("加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleAdd = () => {
    setEditing(null);
    form.resetFields();
    setModalOpen(true);
  };

  const handleEdit = (record: GitTokenConfig) => {
    setEditing(record);
    form.setFieldsValue({ name: record.name, description: record.description, token: "****" });
    setModalOpen(true);
  };

  const handleSubmit = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await updateGitTokenConfig({ id: editing.id, ...values });
        message.success("更新成功");
      } else {
        await addGitTokenConfig(values);
        message.success("新增成功");
      }
      setModalOpen(false);
      load();
    } catch {
      message.error("操作失败");
    }
  };

  const handleActivate = async (id: number) => {
    try {
      await activateGitTokenConfig(id);
      message.success("激活成功");
      load();
    } catch {
      message.error("激活失败");
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteGitTokenConfig(id);
      message.success("删除成功");
      load();
    } catch {
      message.error("删除失败");
    }
  };

  const handleOpenTest = (record: GitTokenConfig) => {
    setTestingRecord(record);
    setTestUrl("");
    setTestModalOpen(true);
  };

  const handleTest = async () => {
    if (!testingRecord || !testUrl.trim()) {
      message.warning("请输入仓库 URL");
      return;
    }
    setTestingId(testingRecord.id);
    try {
      const res = await testGitTokenConfig({ id: testingRecord.id, url: testUrl });
      if (res.success) message.success(res.message);
      else message.warning(res.message);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || "测试请求失败");
    } finally {
      setTestingId(null);
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "Token", dataIndex: "token_masked", key: "token_masked" },
    { title: "描述", dataIndex: "description", key: "description" },
    {
      title: "状态", dataIndex: "is_active", key: "is_active",
      render: (active: boolean) => active ? <Tag color="green">已激活</Tag> : <Tag>未激活</Tag>,
    },
    { title: "创建时间", dataIndex: "created_at", key: "created_at" },
    {
      title: "操作", key: "action",
      render: (_: any, record: GitTokenConfig) => (
        <Space>
          <Button
            size="small"
            type="link"
            loading={testingId === record.id}
            onClick={() => handleOpenTest(record)}
          >
            测试
          </Button>
          {!record.is_active && (
            <Button size="small" type="link" onClick={() => handleActivate(record.id)}>激活</Button>
          )}
          <Button size="small" type="link" onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" type="link" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}><KeyOutlined /> 全局 Git Token 管理</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>新增</Button>
      </div>
      <Table columns={columns} dataSource={configs} rowKey="id" loading={loading} />
      <Modal
        title={editing ? "编辑 Git Token" : "新增 Git Token"}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="如: GitHub PAT" />
          </Form.Item>
          <Form.Item name="token" label="Token" rules={[{ required: true, message: "请输入 Token" }]}>
            <Input.Password placeholder="ghp_xxxx" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`测试 Token: ${testingRecord?.name ?? ""}`}
        open={testModalOpen}
        onOk={handleTest}
        confirmLoading={testingId !== null}
        onCancel={() => setTestModalOpen(false)}
        okText="测试"
      >
        <Form layout="vertical">
          <Form.Item label="Git 仓库 URL" required>
            <Input
              placeholder="https://github.com/org/repo.git"
              value={testUrl}
              onChange={(e) => setTestUrl(e.target.value)}
            />
          </Form.Item>
          <p style={{ fontSize: 12, color: "#999" }}>
            将使用该 Token 对指定仓库执行 git ls-remote 验证可达性（最长 10 秒）
          </p>
        </Form>
      </Modal>
    </div>
  );
};

export default GitTokenManagement;
