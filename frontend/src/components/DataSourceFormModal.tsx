/* ════════════════════════════════════════════
 *  DataSourceFormModal — 添加数据源对话框
 *
 *  包含: db_type/host/port/database/username/password/description 字段
 *       + 时区 AutoComplete combobox
 *       + "测试连通性" probe 按钮 + 状态机
 *       + 确定按钮置灰 (需先 probe ok 或 probe need-tz 且填了时区)
 * ════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import {
  AutoComplete,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  message,
} from "antd";
import * as api from "@/api";
import type { DbType } from "@/types";
import { DB_TYPE_META } from "@/types";

/* ── 时区选项: 由 Intl.supportedValuesOf 动态生成 ── */
const TZ_OPTIONS: { value: string }[] =
  typeof Intl !== "undefined" && typeof Intl.supportedValuesOf === "function"
    ? Intl.supportedValuesOf("timeZone").map((tz) => ({ value: tz }))
    : [];

/* ── probe 状态机类型 ── */
type ProbeStatus = "idle" | "probing" | "ok" | "need-tz" | "fail";

interface ProbeState {
  status: ProbeStatus;
  reason?: string;
}

/* ── 组件 Props ── */
export interface DataSourceFormModalProps {
  open: boolean;
  activeNsId: number | null;
  onCancel: () => void;
  onSubmitted: () => void; // 成功添加后由父组件触发刷新
}

const DataSourceFormModal: React.FC<DataSourceFormModalProps> = ({
  open,
  activeNsId,
  onCancel,
  onSubmitted,
}) => {
  const [form] = Form.useForm();
  /* 监听 db_type 用于 Oracle Service Name label */
  const dbType = Form.useWatch<string>("db_type", form);
  /* 监听时区字段, 用于 canSubmit 计算 */
  const timezoneValue = Form.useWatch<string>("timezone", form);

  const [probeState, setProbeState] = useState<ProbeState>({ status: "idle" });
  const [probing, setProbing] = useState(false);

  /* ── modal 打开/关闭时重置状态 ── */
  useEffect(() => {
    if (!open) {
      setProbeState({ status: "idle" });
      setProbing(false);
    }
  }, [open]);

  /* ── probe 连通但测不出时区 (need-tz): 焦点跳时区框 (design 组件F) ── */
  useEffect(() => {
    if (probeState.status === "need-tz") {
      document.getElementById("timezone")?.focus();
    }
  }, [probeState.status]);

  /* ── 选择 db_type 自动填充默认端口 ── */
  const handleValuesChange = (changed: Record<string, unknown>) => {
    if (changed.db_type) {
      const meta = DB_TYPE_META[changed.db_type as DbType];
      if (meta) form.setFieldValue("port", meta.defaultPort);
    }
    /* 时区有值后, 从 need-tz 状态恢复为可提交 (不改 probe 结果本身) */
  };

  /* ── 测试连通性 ── */
  const handleProbe = async () => {
    if (!activeNsId) return;
    try {
      await form.validateFields([
        "db_type", "host", "port", "database", "username", "password",
      ]);
    } catch {
      return; // 表单校验失败, 不发请求
    }

    const vals = form.getFieldsValue([
      "db_type", "host", "port", "database", "username", "password",
    ]);
    // probe body 不含 timezone (L1 契约锁定)
    const body: Record<string, unknown> = {
      db_type: vals.db_type,
      host: vals.host,
      port: vals.port,
      database: vals.database,
      username: vals.username,
      password: vals.password,
    };

    setProbing(true);
    setProbeState({ status: "probing" });
    try {
      const res = await api.probeDatasource(activeNsId, body);
      if (res.connected && res.detected_timezone) {
        form.setFieldValue("timezone", res.detected_timezone);
        setProbeState({ status: "ok" });
      } else if (res.connected && !res.detected_timezone) {
        setProbeState({ status: "need-tz" });
      } else {
        setProbeState({
          status: "fail",
          reason: res.failure_reason ?? "连接失败",
        });
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      const detail =
        err?.response?.data?.detail ?? err?.message ?? "连接失败";
      setProbeState({ status: "fail", reason: detail });
    } finally {
      setProbing(false);
    }
  };

  /* ── 确定按钮是否可点 (ok/need-tz 均要求时区非空, 防止 ok 态清空时区绕过校验) ── */
  const canSubmit =
    !!timezoneValue &&
    (probeState.status === "ok" || probeState.status === "need-tz");

  /* ── 提交表单 ── */
  const handleOk = async () => {
    if (!activeNsId) return;
    const vals = await form.validateFields();
    try {
      await api.addDataSource(activeNsId, vals);
      message.success("数据源添加成功");
      form.resetFields();
      setProbeState({ status: "idle" });
      onSubmitted();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      const detail = err?.response?.data?.detail ?? "连接失败, 请检查连接信息";
      message.error(`数据源添加失败: ${detail}`);
    }
  };

  /* ── 取消 ── */
  const handleCancel = () => {
    form.resetFields();
    setProbeState({ status: "idle" });
    onCancel();
  };

  return (
    <Modal
      title="添加数据源"
      open={open}
      onOk={handleOk}
      onCancel={handleCancel}
      okButtonProps={{ disabled: !canSubmit }}
      okText="确定"
      cancelText="取消"
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        onValuesChange={handleValuesChange}
      >
        <Form.Item name="db_type" label="类型" rules={[{ required: true }]}>
          <Select
            options={Object.entries(DB_TYPE_META).map(([v, m]) => ({
              value: v,
              label: m.label,
            }))}
          />
        </Form.Item>

        <Form.Item name="host" label="主机" rules={[{ required: true }]}>
          <Input placeholder="localhost" />
        </Form.Item>

        <Form.Item name="port" label="端口" rules={[{ required: true }]}>
          <InputNumber style={{ width: "100%" }} />
        </Form.Item>

        {/* Oracle 的 database 字段含义是 Service Name */}
        <Form.Item
          name="database"
          label={dbType === "oracle" ? "Service Name" : "数据库"}
          tooltip={
            dbType === "oracle" ? "Oracle Service Name, 例如 orclpdb" : undefined
          }
          rules={[{ required: true }]}
        >
          <Input placeholder={dbType === "oracle" ? "orclpdb" : undefined} />
        </Form.Item>

        <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
          <Input />
        </Form.Item>

        <Form.Item name="password" label="密码" rules={[{ required: true }]}>
          <Input.Password />
        </Form.Item>

        <Form.Item
          name="description"
          label="用途描述"
          tooltip="这个库存什么数据, 便于 AI 理解 (选填)"
        >
          <Input.TextArea rows={2} placeholder="例: 订单交易库 / 设备运维数据" />
        </Form.Item>

        {/* 测试连通性按钮 */}
        <Form.Item>
          <Button
            onClick={handleProbe}
            loading={probing}
            data-testid="probe-btn"
          >
            测试连通性
          </Button>
          {probeState.status === "ok" && (
            <span style={{ marginLeft: 8, color: "green" }}>连接成功</span>
          )}
        </Form.Item>

        {/* 时区 combobox */}
        <Form.Item
          name="timezone"
          label="时区"
          tooltip="probe 成功后自动填入; 无法检测时手动选择"
        >
          <AutoComplete
            options={TZ_OPTIONS}
            placeholder="Start typing..."
            filterOption={(input, opt) =>
              (opt?.value as string)
                .toLowerCase()
                .includes(input.toLowerCase())
            }
            data-testid="timezone-input"
          />
        </Form.Item>

        {/* 错误/警告提示 */}
        {probeState.status === "need-tz" && !timezoneValue && (
          <div style={{ color: "red", marginBottom: 8 }}>
            必须选择时区
          </div>
        )}
        {probeState.status === "fail" && (
          <div style={{ color: "red", marginBottom: 8 }}>
            {probeState.reason}
          </div>
        )}
      </Form>
    </Modal>
  );
};

export default DataSourceFormModal;
