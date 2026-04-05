/**
 * 排序工具函数
 * 提供各列表页面的纯函数排序逻辑，无副作用
 */

import type { Material } from '../store/types';

/**
 * 按最新上传时间排序（降序）
 * @param materials - 资料列表
 * @returns 排序后的新数组
 */
export function sortByNewest(materials: Material[]): Material[] {
  return [...materials].sort((a, b) => b.uploadTimestamp - a.uploadTimestamp);
}

/**
 * 按最早上传时间排序（升序）
 * @param materials - 资料列表
 * @returns 排序后的新数组
 */
export function sortByOldest(materials: Material[]): Material[] {
  return [...materials].sort((a, b) => a.uploadTimestamp - b.uploadTimestamp);
}

/**
 * 按名称字母序排序（升序）
 * @param materials - 资料列表
 * @returns 排序后的新数组
 */
export function sortByName(materials: Material[]): Material[] {
  return [...materials].sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'));
}

/**
 * 按文件大小排序（降序，最大在前）
 * @param materials - 资料列表
 * @returns 排序后的新数组
 */
export function sortBySize(materials: Material[]): Material[] {
  return [...materials].sort((a, b) => b.sizeBytes - a.sizeBytes);
}

/**
 * 对资料列表应用排序
 * @param materials - 原始资料列表
 * @param sortKey - 排序键
 * @returns 排序后的新数组
 */
export function sortMaterials(
  materials: Material[],
  sortKey: 'newest' | 'oldest' | 'name' | 'size',
): Material[] {
  switch (sortKey) {
    case 'newest':
      return sortByNewest(materials);
    case 'oldest':
      return sortByOldest(materials);
    case 'name':
      return sortByName(materials);
    case 'size':
      return sortBySize(materials);
    default:
      return materials;
  }
}

/**
 * 对成品列表应用排序
 * @param products - 成品列表
 * @param sortKey - 排序键
 * @returns 排序后的新数组
 */
export function sortProducts<T extends { createdAt: string; useCount: number; rating: number }>(
  products: T[],
  sortKey: '最新发布' | '使用最多' | '评分最高',
): T[] {
  switch (sortKey) {
    case '最新发布':
      return [...products].sort(
        (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      );
    case '使用最多':
      return [...products].sort((a, b) => b.useCount - a.useCount);
    case '评分最高':
      return [...products].sort((a, b) => b.rating - a.rating);
    default:
      return products;
  }
}
