"""测试 utils.py 工具函数"""
from datetime import datetime, timezone, timedelta
from mini_ai.utils import now_ts


class TestNowTs:
    """测试 now_ts 时间戳函数"""
    
    def test_format_is_correct(self):
        """时间戳格式应为 YYYY-MM-DDTHH:MM:SS"""
        result = now_ts()
        
        # 应能解析为 datetime
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S")
        assert dt is not None
    
    def test_timezone_is_utc8(self):
        """时间戳应使用 UTC+8 时区"""
        result = now_ts()
        
        # 解析时间
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S")
        
        # 与当前 UTC+8 时间比较（允许 1 秒误差）
        utc8 = timezone(timedelta(hours=8))
        now_utc8 = datetime.now(utc8).replace(tzinfo=None)
        
        # 时间差应小于 2 秒
        diff = abs((dt - now_utc8).total_seconds())
        assert diff < 2.0
    
    def test_returns_string(self):
        """应返回字符串类型"""
        result = now_ts()
        assert isinstance(result, str)
    
    def test_length_is_fixed(self):
        """时间戳长度固定为 19 字符"""
        result = now_ts()
        assert len(result) == 19
    
    def test_components_valid(self):
        """时间戳各组成部分应在有效范围内"""
        result = now_ts()
        
        # 提取组成部分
        year = int(result[0:4])
        month = int(result[5:7])
        day = int(result[8:10])
        hour = int(result[11:13])
        minute = int(result[14:16])
        second = int(result[17:19])
        
        # 验证范围
        assert 2024 <= year <= 2100  # 合理年份范围
        assert 1 <= month <= 12
        assert 1 <= day <= 31
        assert 0 <= hour <= 23
        assert 0 <= minute <= 59
        assert 0 <= second <= 59
    
    def test_separators_correct(self):
        """分隔符应为 T 和 :"""
        result = now_ts()
        
        assert result[4] == "-"
        assert result[7] == "-"
        assert result[10] == "T"
        assert result[13] == ":"
        assert result[16] == ":"
