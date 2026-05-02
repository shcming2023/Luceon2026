# Luceon2026 PRD Index

> 本目录只允许存在一份当前有效 PRD。其它方案、评审、复盘和任务书只能作为输入材料，不得被引用为当前需求事实源。

## 当前有效 PRD

- 当前唯一有效 PRD：[Luceon2026-PRD-v0.4.md](./Luceon2026-PRD-v0.4.md)
- PRD 维护角色：[luplan PRD 维护规程](./luplan-prd-maintenance.md)
- 指挥与验收角色：Lucia
- 外部实现角色：lucode

## 唯一性规则

1. `docs/prd/Luceon2026-PRD-v0.4.md` 是当前唯一有效 PRD。
2. `docs/reviews/` 下的 PRD、评审报告、方案和复盘均为历史输入或专项分析，不自动成为当前需求。
3. 新需求进入实现前，必须由 Lucia 指挥 luplan 判断其属于：
   - 已确定需求：写入当前有效 PRD 的主体章节或验收标准。
   - 调试策略：写入 PRD 的策略/风险/待验证区，不得冒充稳定需求。
   - 历史记录：写入 PRD 变更记录或相关专项报告。
4. 如未来需要升级 PRD 版本，必须先由 Lucia 明确宣布旧版废止、新版成为唯一有效 PRD，并同步更新本索引。

## luplan 工作入口

luplan 不直接修改业务代码。luplan 只负责维护 PRD 契约、记录迭代依据、区分稳定需求与调试策略，并在每次维护后向 Lucia 汇报变更摘要和影响范围。

