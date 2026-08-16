"""场景化提示词模板（4.1）：按图型提供 STRUCTURED_PROMPT 专用变体。

基础提示词（engine.STRUCTURED_PROMPT）之上的图型专用追加指令，
供 CLI `--type` / 脚本选择；未指定时用 default（即基础提示词）。
"""

from .engine import STRUCTURED_PROMPT

_TEMPLATE_SUFFIXES = {
    "geometry": (
        "\n【几何图专用】本图是几何题图：必须把点/线段/角/圆完整填入 figures，"
        "标注每个顶点坐标、每条线段长度、每个角度（度）与半径；"
        "角的边用两个大写字母（如 AB）或单字母表示；"
        "圆必须列出圆上点（circles.on，图中所有位于圆周上的点）；"
        "若图中有平行四边形/矩形/菱形等特殊四边形，"
        "用 quads 声明类型与顶点顺序。"
    ),
    "function": (
        "\n【函数图专用】本图是函数图像：重点读出坐标系、轴刻度、零点/交点/顶点坐标，"
        "把它们作为 points 填入 figures（坐标为图上实际刻度值），"
        "并把函数表达式/定义域写入 note。"
        "若是抛物线：零点命名为 z1、z2（y≈0 的点），顶点命名为 vertex，"
        "以便校验『对称轴过两零点中点』。"
    ),
    "vector": (
        "\n【向量图专用】本图是向量/矢量图：把所有向量完整列入 vectors（分量式 [dx, dy] "
        "或起终点 [x1,y1,x2,y2]），同组向量维度必须一致；向量加法关系（如 a1+a2=b）写入 note。"
    ),
    "statistics": (
        "\n【统计图专用】本图是统计图表：读出所有类别与数值序列（柱高/折线点/扇区百分比），"
        "数值序列长度必须与类别数一致；百分比类数据之和应≈100。"
    ),
}

PROMPT_TEMPLATES: dict[str, str] = {"default": STRUCTURED_PROMPT}
for _key, _suffix in _TEMPLATE_SUFFIXES.items():
    PROMPT_TEMPLATES[_key] = STRUCTURED_PROMPT + _suffix

__all__ = ["PROMPT_TEMPLATES"]
