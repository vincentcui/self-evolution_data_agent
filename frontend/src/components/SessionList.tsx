/* ════════════════════════════════════════════
 *  SessionList — 侧边栏会话列表
 * ----------------------------------------------------------------------------
 *  接收 namespaceId, 展示该空间下的会话列表.
 *  支持新建、切换、重命名、删除.
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Button, Dropdown, Input, message, Popconfirm, Spin } from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  MoreOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import type { Session } from "@/types";


interface Props {
  namespaceId: number | null;
  sessions: Session[];
  activeSessionId: string | null;
  loading: boolean;
  ready?: boolean;
  onSelect: (id: string | null) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

const SessionList: React.FC<Props> = ({
  namespaceId,
  sessions,
  ready,
  activeSessionId,
  loading,
  onSelect,
  onRename,
  onDelete,
}) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [hoverList, setHoverList] = useState(false);
  const [scrolledBottom, setScrolledBottom] = useState(false);
  const [overflowing, setOverflowing] = useState(false);

  if (namespaceId == null) {
    return (
      <div style={{ padding: "12px 16px", color: "#999", fontSize: 12 }}>
        请先选择命名空间
      </div>
    );
  }

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
        <div style={{ position: "relative", flex: 1 }}>
          <div
            style={{ overflowY: hoverList ? "auto" : "hidden", maxHeight: "calc(100vh - 300px)" }}
            onMouseEnter={() => setHoverList(true)}
            onMouseLeave={() => setHoverList(false)}
            onScroll={(e) => {
              const t = e.currentTarget;
              setOverflowing(t.scrollHeight > t.clientHeight);
              setScrolledBottom(t.scrollHeight - t.scrollTop - t.clientHeight < 20);
            }}
          >
            {sessions.map((session) => {
            const menuItems: MenuProps["items"] = [
              { key: "rename", icon: <EditOutlined />, label: "重命名" },
              { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true },
            ];
            const isActive = session.id === activeSessionId;
            return (
              <div
                key={session.id}
                onClick={() => onSelect(session.id)}
                onContextMenu={(e) => { e.preventDefault(); }}
                style={{
                  cursor: "pointer",
                  padding: "10px 12px",
                  margin: "2px 8px",
                  borderRadius: 8,
                  background: isActive ? "#f0f0f0" : "transparent",
                  transition: "background 0.15s",
                  position: "relative",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = isActive ? "#e8e8e8" : "#f5f5f5")}
                onMouseLeave={(e) => (e.currentTarget.style.background = isActive ? "#f0f0f0" : "transparent")}
              >
                {editingId === session.id ? (
                  <Input
                    size="small"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    onPressEnter={handleConfirmRename}
                    onBlur={handleConfirmRename}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                    style={{ fontSize: 13 }}
                  />
                ) : (
                  <>
                    <span
                      title={session.title}
                      style={{
                        display: "block",
                        fontSize: 13,
                        fontWeight: isActive ? 500 : 400,
                        color: isActive ? "#1a1a1a" : "#555",
                        lineHeight: "20px",
                        overflow: "hidden",
                        whiteSpace: "nowrap",
                        WebkitMaskImage: "linear-gradient(to right, black calc(100% - 36px), transparent 100%)",
                        maskImage: "linear-gradient(to right, black calc(100% - 36px), transparent 100%)",
                      }}
                    >
                      {session.title}
                    </span>
                    <span style={{ fontSize: 11, color: "#bbb", lineHeight: "16px" }}>
                      {formatTime(session.updated_at)}
                    </span>
                  </>
                )}
                <Popconfirm
                  open={deletingId === session.id}
                  title="确定删除此会话？"
                  onConfirm={(e) => { e?.stopPropagation(); handleDelete(session.id); setDeletingId(null); }}
                  onCancel={(e) => { e?.stopPropagation(); setDeletingId(null); }}
                  okText="确定"
                  cancelText="取消"
                >
                  <Dropdown
                    menu={{
                      items: menuItems,
                      onClick: ({ key, domEvent }) => {
                        domEvent.stopPropagation();
                        if (key === "rename") handleStartRename(session);
                        if (key === "delete") setDeletingId(session.id);
                      },
                    }}
                    trigger={["contextMenu", "click"]}
                  >
                  <Button
                    type="text"
                    size="small"
                    icon={<MoreOutlined />}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      position: "absolute",
                      right: 4,
                      top: 6,
                      opacity: 0.4,
                      fontSize: 14,
                    }}
                  />
                </Dropdown>
                </Popconfirm>
              </div>
            );
          })}
        </div>
        {/* 底部渐隐提示 */}
        {overflowing && !scrolledBottom && (
          <div style={{
            position: "absolute", bottom: 0, left: 0, right: 0, height: 60,
            background: "linear-gradient(rgba(255,255,255,0), rgba(255,255,255,0.7) 25%, #fff 55%)",
            pointerEvents: "none",
            zIndex: 1,
          }} />
        )}
      </div>
      )}
    </div>
  );
};

export default SessionList;
