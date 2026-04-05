/**
 * 分页工具 Hook
 * 封装纯前端分页逻辑，提供当前页数据、页码控制和总页数
 */

import { useState, useMemo, useEffect } from 'react';

/** 每页显示条数 */
export const ITEMS_PER_PAGE = 12;

/**
 * 分页 Hook 返回值
 */
export interface UsePaginationReturn<T> {
  /** 当前页的数据切片 */
  currentItems: T[];
  /** 当前页码（从 1 开始） */
  currentPage: number;
  /** 总页数 */
  totalPages: number;
  /** 跳转到指定页 */
  goToPage: (page: number) => void;
  /** 上一页 */
  prevPage: () => void;
  /** 下一页 */
  nextPage: () => void;
  /** 是否有上一页 */
  hasPrev: boolean;
  /** 是否有下一页 */
  hasNext: boolean;
}

/**
 * 分页 Hook
 * 当数据源变化（如筛选后）自动重置到第一页
 * @param data - 完整的数据列表
 * @param itemsPerPage - 每页条数，默认 ITEMS_PER_PAGE
 * @returns 分页控制对象
 */
export function usePagination<T>(
  data: T[],
  itemsPerPage: number = ITEMS_PER_PAGE,
): UsePaginationReturn<T> {
  const [currentPage, setCurrentPage] = useState(1);

  // 数据源变化时重置为第一页（如筛选条件变化）
  useEffect(() => {
    setCurrentPage(1);
  }, [data.length]);

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil(data.length / itemsPerPage)),
    [data.length, itemsPerPage],
  );

  const currentItems = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage;
    return data.slice(start, start + itemsPerPage);
  }, [data, currentPage, itemsPerPage]);

  const goToPage = (page: number) => {
    const clamped = Math.min(Math.max(1, page), totalPages);
    setCurrentPage(clamped);
  };

  const prevPage = () => goToPage(currentPage - 1);
  const nextPage = () => goToPage(currentPage + 1);

  return {
    currentItems,
    currentPage,
    totalPages,
    goToPage,
    prevPage,
    nextPage,
    hasPrev: currentPage > 1,
    hasNext: currentPage < totalPages,
  };
}

/**
 * 生成分页页码数组（含省略号）
 * @param currentPage - 当前页
 * @param totalPages - 总页数
 * @returns 页码数组，省略号用 '...' 表示
 */
export function getPageNumbers(currentPage: number, totalPages: number): (number | '...')[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }

  const pages: (number | '...')[] = [1];

  if (currentPage > 3) pages.push('...');

  const start = Math.max(2, currentPage - 1);
  const end = Math.min(totalPages - 1, currentPage + 1);

  for (let i = start; i <= end; i++) {
    pages.push(i);
  }

  if (currentPage < totalPages - 2) pages.push('...');
  pages.push(totalPages);

  return pages;
}
