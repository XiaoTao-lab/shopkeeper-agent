/**
 * 十进制字符串精确比较
 *
 * 不经浮点数，支持任意精度。用于表格排序时比较金额、大整数等。
 * 规则：
 * - 校验格式：只接受后端生成的规范十进制字符串
 * - 分离符号、整数、小数部分
 * - 补齐小数位数后转 BigInt 比较
 * - null 值在排序时放末尾（双向）
 */

const DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const INTEGER_PATTERN = /^-?(?:0|[1-9][0-9]*)$/;

export function isValidDecimal(value: string, kind: "decimal" | "integer"): boolean {
  const pattern = kind === "decimal" ? DECIMAL_PATTERN : INTEGER_PATTERN;
  return pattern.test(value);
}

/**
 * 比较两个十进制字符串
 * @returns 负数表示 a < b，0 表示相等，正数表示 a > b
 */
export function compareDecimal(a: string, b: string): number {
  // 处理符号
  const aNeg = a.startsWith("-");
  const bNeg = b.startsWith("-");
  const aAbs = aNeg ? a.slice(1) : a;
  const bAbs = bNeg ? b.slice(1) : b;

  // 分离整数和小数部分
  const [aInt, aFrac = ""] = aAbs.split(".");
  const [bInt, bFrac = ""] = bAbs.split(".");

  // 补齐小数位数
  const maxFracLen = Math.max(aFrac.length, bFrac.length);
  const aFracPadded = aFrac.padEnd(maxFracLen, "0");
  const bFracPadded = bFrac.padEnd(maxFracLen, "0");

  // 转 BigInt 比较（整数部分 + 小数部分拼接）
  const aBig = BigInt(aInt + aFracPadded);
  const bBig = BigInt(bInt + bFracPadded);

  if (aNeg && !bNeg) return -1;
  if (!aNeg && bNeg) return 1;
  if (aNeg && bNeg) {
    // 两个都是负数，绝对值大的反而小
    return aBig > bBig ? -1 : aBig < bBig ? 1 : 0;
  }
  // 两个都是正数
  return aBig > bBig ? 1 : aBig < bBig ? -1 : 0;
}

/**
 * 排序比较器工厂
 * @param kind 列类型
 * @param direction 排序方向
 * @returns 比较函数，null 值放末尾
 */
export function makeComparator(kind: string, direction: "asc" | "desc") {
  return (a: unknown, b: unknown): number => {
    // null/undefined 放末尾（双向）
    if (a === null || a === undefined) return b === null || b === undefined ? 0 : 1;
    if (b === null || b === undefined) return -1;

    const aStr = String(a);
    const bStr = String(b);

    let cmp = 0;
    if (kind === "decimal" || kind === "integer") {
      if (isValidDecimal(aStr, kind) && isValidDecimal(bStr, kind)) {
        cmp = compareDecimal(aStr, bStr);
      } else {
        // 非法数值快照，按文本比较
        cmp = aStr.localeCompare(bStr);
      }
    } else if (kind === "number") {
      const aNum = Number(aStr);
      const bNum = Number(bStr);
      if (Number.isFinite(aNum) && Number.isFinite(bNum)) {
        cmp = aNum - bNum;
      } else {
        cmp = aStr.localeCompare(bStr);
      }
    } else if (kind === "boolean") {
      cmp = (aStr === "true" ? 1 : 0) - (bStr === "true" ? 1 : 0);
    } else if (kind === "date" || kind === "datetime") {
      cmp = aStr.localeCompare(bStr);
    } else {
      cmp = aStr.localeCompare(bStr);
    }

    return direction === "desc" ? -cmp : cmp;
  };
}
