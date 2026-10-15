/**
 * 演示账号与角色展示定义
 *
 * 注意这里是**账号**列表，不是角色列表——这是本次鉴权改造的关键区别：
 * 前端只能选择"用哪个账号登录"，角色由服务端按账号查表后写进令牌，
 * 前端既无法指定、也无法篡改自己能拿到什么权限。
 *
 * 账号映射与后端 conf/app_config.yaml 的 auth.dev_users 保持一致。
 */

export type DemoUser = {
  value: string;
  label: string;
};

/** 演示账号（生产接真实登录后，这份清单应整体删掉） */
export const DEMO_USERS: DemoUser[] = [
  { value: "boss", label: "boss · 管理员" },
  { value: "east", label: "east · 华东区经理" },
  { value: "analyst", label: "analyst · 分析师" },
  { value: "guest", label: "guest · 访客" },
];

export type RoleOption = {
  value: string;
  label: string;
};

/** 角色展示名。角色值来自令牌，这里只负责翻译成人话 */
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
