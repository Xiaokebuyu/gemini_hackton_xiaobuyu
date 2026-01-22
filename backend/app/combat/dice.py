"""
骰子系统

实现标准 DND 骰子记号解析和投掷
"""
import random
import re
from typing import List, Tuple


class DiceRoller:
    """骰子投掷器"""

    @staticmethod
    def roll(dice_notation: str) -> Tuple[int, List[int]]:
        """
        投掷骰子

        Args:
            dice_notation: 骰子记号（如 "1d20", "2d6", "3d8+2"）

        Returns:
            Tuple[int, List[int]]: (总值, 各骰子结果列表)

        Examples:
            >>> roll("1d20")
            (15, [15])
            >>> roll("2d6+3")
            (11, [4, 4])  # 4+4+3=11
        """
        # 解析骰子记号
        pattern = r"(\d+)d(\d+)([+-]\d+)?"
        match = re.match(pattern, dice_notation.lower().replace(" ", ""))

        if not match:
            raise ValueError(f"Invalid dice notation: {dice_notation}")

        num_dice = int(match.group(1))
        die_size = int(match.group(2))
        modifier = int(match.group(3)) if match.group(3) else 0

        # 投掷
        rolls = [random.randint(1, die_size) for _ in range(num_dice)]
        total = sum(rolls) + modifier

        return total, rolls

    @staticmethod
    def roll_single(die_size: int) -> int:
        """
        投掷单个骰子

        Args:
            die_size: 骰子面数（如20表示d20）

        Returns:
            int: 结果（1到die_size）
        """
        return random.randint(1, die_size)

    @staticmethod
    def roll_with_modifier(dice_notation: str, modifier: int) -> int:
        """
        投掷骰子并加修正值

        Args:
            dice_notation: 骰子记号
            modifier: 修正值

        Returns:
            int: 总值
        """
        total, _ = DiceRoller.roll(dice_notation)
        return total + modifier


# 便捷函数
def d20() -> int:
    """投掷 d20"""
    return random.randint(1, 20)


def d6() -> int:
    """投掷 d6"""
    return random.randint(1, 6)


def d4() -> int:
    """投掷 d4"""
    return random.randint(1, 4)
