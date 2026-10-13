/**
 * 角色定义
 * 与后端 conf/app_config.yaml 的 rbac.roles 保持一致，供前端身份切换与权限提示展示使用
 */

export type RoleOption = {
  value: string;
  label: string;
};

export const ROLES: RoleOption[] = [
  { value: "admin", label: "管理员" },
  { value: "east_manager", label: "华东区经理" },
  { value: "analyst", label: "分析师" },
  { value: "guest", label: "访客" },
];

export function roleLabel(value?: string): string {
  if (!value) return "未知角色";
  return ROLES.find((role) => role.value === value)?.label ?? value;
}
