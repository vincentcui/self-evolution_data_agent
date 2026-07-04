/* ════════════════════════════════════════════
 *  SessionList — 侧边栏会话列表
 * ----------------------------------------------------------------------------
 *  接收 namespaceId, 展示该空间下的会话列表.
 *  支持新建、切换、重命名、删除.
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Button, Input, List, message, Modal, Popconfirm, Spin, Typography } from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  MessageOutlined,
} from "@ant-design/icons";
import type { Session } from "@/types";

const { Text } = Typography;

interface Props {
  namespaceId: number | null;
  sessions: Session[];
  activeSessionId: string | null;
  loading: boolean;
  onCreate: (nsId: number) => Promise<Session>;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

const SessionList: React.FC<Props> = ({
  namespaceId,
  sessions,
  activeSessionId,
  loading,
  onCreate,
  onSelect,
  onRename,
  onDelete,
}) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  if (namespaceId == null) {
    return (
      <div style={{ padding: "12px 16px", color: "#999", fontSize: 12 }}>
        请先选择命名空间
      </div>
    );
  }

  const handleCreate = async () => {
    try {
      await onCreate(namespaceId);
    } catch {
      message.error("创建会话失败");
    }
  };

  const handleStartRename = (session: Session) => {
    setEditingId(session.id);
    setEditTitle(session.title);
  };

  const handleConfirmRename = async () => {
    if (!editingId || !editTitle.trim()) return;
    try {
      await onRename(editingId, editTitle.trim());
      setEditingId(null);
    } catch {
      message.error("重命名失败");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await onDelete(id);
    } catch {
      message.error("删除失败");
    }
  };

  const formatTime = (t: string | null) => {
    if (!t) return "";
    const d = new Date(t);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    if (diff < 60_000) return "刚刚";
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  };

  return (
    <div style={{ padding: "0 0 8px 0" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "8px 16px",
        }}
      >
        <Text strong style={{ fontSize: 12, color: "#666" }}>
          会话
        </Text>
        <Button
          type="text"
          size="small"
          icon={<PlusOutlined />}
          onClick={handleCreate}
          loading={loading}
        />
      </div>

      {/* List */}
      {loading && sessions.length === 0 ? (
        <div style={{ textAlign: "center", padding: 16 }}>
          <Spin size="small" />
        </div>
      ) : sessions.length === 0 ? (
        <div style={{ padding: "12px 16px", color: "#bbb", fontSize: 12 }}>
          暂无会话
        </div>
      ) : (
        <List
          size="small"
          dataSource={sessions}
          split={false}
          style={{ maxHeight: "calc(100vh - 300px)", overflowY: "auto" }}
          renderItem={(session) => (
            <List.Item
              key={session.id}
              onClick={() => onSelect(session.id)}
              style={{
                cursor: "pointer",
                padding: "4px 16px",
                background:
                  session.id === activeSessionId ? "#e6f4ff" : "transparent",
                borderInlineEnd:
                  session.id === activeSessionId
                    ? "3px solid #1677ff"
                    : "3px solid transparent",
              }}
              actions={
                session.id === activeSessionId
                  ? [
                      <Button
                        key="edit"
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleStartRename(session);
                        }}
                      />,
                      <Popconfirm
                        key="delete"
                        title="确定删除此会话？"
                        onConfirm={(e) => {
                          e?.stopPropagation();
                          handleDelete(session.id);
                        }}
                        onCancel={(e) => e?.stopPropagation()}
                        okText="确定"
                        cancelText="取消"
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Popconfirm>,
                    ]
                  : undefined
              }
            >
              <List.Item.Meta
                avatar={
                  <MessageOutlined
                    style={{
                      color:
                        session.id === activeSessionId ? "#1677ff" : "#bbb",
                      fontSize: 14,
                    }}
                  />
                }
                title={
                  editingId === session.id ? (
                    <Input
                      size="small"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      onPressEnter={handleConfirmRename}
                      onBlur={handleConfirmRename}
                      autoFocus
                      onClick={(e) => e.stopPropagation()}
                      style={{ width: "calc(100% - 30px)" }}
                    />
                  ) : (
                    <Text
                      ellipsis={{ tooltip: session.title }}
                      style={{
                        fontSize: 13,
                        fontWeight:
                          session.id === activeSessionId ? 500 : 400,
                        color:
                          session.id === activeSessionId ? "#1677ff" : "#333",
                      }}
                    >
                      {session.title}
                    </Text>
                  )
                }
                description={
                  <Text style={{ fontSize: 11, color: "#bbb" }}>
                    {formatTime(session.updated_at)}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
};

export default SessionList;
