# https://quantpedia.com/strategies/trading-on-the-dividend-paydate/
#
# The investment universe consists of stocks from NYSE, AMEX and NASDAQ that offer company-sponsored DRIPs.
# Each day at close investors buy stocks which have dividend payday on the next working day and hold these stocks
# for one day. Stocks are weighted equally.
#
# QC implementation:

# region imports
from AlgorithmImports import *
from datetime import datetime
from pandas.tseries.offsets import BDay



# endregion

class TradingDividendPaydate(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2012, 9, 18)
        self.SetCash(100000)

        symbol = self.AddEquity('SPY', Resolution.Minute).Symbol

        # Store drip tickers.
        # Source: http://www.dripdatabase.com/DRIP_Directory_AtoZ.aspx
        csv_string_file = self.Download('data.quantpedia.com/backtesting_data/economic/drip_tickers.csv')
        lines = csv_string_file.split('\r\n')
        self.drip_tickers = [x for x in lines[1:]]

        # dividend data
        self.dividend_data = {}  # dict of dicts indexed by paydate date

        csv_string_file = self.Download('data.quantpedia.com/backtesting_data/economic/dividend_dates.csv')
        lines = csv_string_file.split('\r\n')
        for line in lines[3:]:  # skip first three comment lines
            if line == '':
                continue

            line_split = line.split(';')
            ex_div_date = datetime.strptime(line_split[0], "%Y-%m-%d").date()

            # N stocks -> n*6 properties
            for i in range(1, len(line_split), 6):
                # parse dividend info
                ticker = str(line_split[i])
                payday = datetime.strptime(line_split[i + 1], "%m/%d/%Y").date() if line_split[i + 1] != '' else None

                if payday not in self.dividend_data:
                    self.dividend_data[payday] = {}

                record_date = datetime.strptime(line_split[i + 2], "%m/%d/%Y").date() if line_split[
                                                                                             i + 2] != '' else None
                dividend_value = float(line_split[i + 3]) if line_split[i + 3] != '' else None
                ann_dividend_value = float(line_split[i + 4]) if line_split[i + 4] != '' else None
                announcement_date = datetime.strptime(line_split[i + 5], "%m/%d/%Y").date() if line_split[
                                                                                                   i + 5] != '' else None

                # store ticker dividend info to current ex-div date
                self.dividend_data[payday][ticker] = DividendInfo(ticker, ex_div_date, payday, record_date,
                                                                  dividend_value, ann_dividend_value, announcement_date)

        self.active_universe = []  # selected stock universe
        self.selection_flag = False
        self.UniverseSettings.Resolution = Resolution.Minute
        self.AddUniverse(self.CoarseSelectionFunction, self.FineSelectionFunction)
        self.Schedule.On(self.DateRules.MonthEnd(symbol), self.TimeRules.AfterMarketOpen(symbol), self.Selection)
        self.Schedule.On(self.DateRules.EveryDay(symbol), self.TimeRules.BeforeMarketClose(symbol, 16), self.Rebalance)

    def OnSecuritiesChanged(self, changes):
        for security in changes.AddedSecurities:
            security.SetFeeModel(CustomFeeModel())

    def CoarseSelectionFunction(self, coarse):
        if not self.selection_flag:
            return Universe.Unchanged

        self.selection_flag = False

        selected = [x.Symbol for x in coarse if x.Symbol.Value in self.drip_tickers]
        return selected

    """ ALL CHANGES FROM ORIGINAL ALGO ARE FOUND IN THIS FUNCTION"""
    def FineSelectionFunction(self, fine):
        fine = [x for x in fine if x.MarketCap != 0 and \
                ((x.SecurityReference.ExchangeId == "NYS") or (x.SecurityReference.ExchangeId == "NAS") or (
                            x.SecurityReference.ExchangeId == "ASE"))]

        # filtering stocks with valid price, PERatio and BasicEPS
        fine = [x for x in fine if
                x.Price > 0 and x.ValuationRatios.PERatio > 0 and x.EarningReports.BasicEPS.TwelveMonths > 0 and \
                x.FinancialStatements.CashFlowStatement.OperatingCashFlow.TwelveMonths is not None and x.FinancialStatements.IncomeStatement.NetIncome.TwelveMonths is not None and \
                x.FinancialStatements.IncomeStatement.NetIncome.TwelveMonths != 0]

        # calculating dividend yield
        for x in fine:
            payout_ratio = 1 - (
                        x.FinancialStatements.CashFlowStatement.OperatingCashFlow.TwelveMonths / x.FinancialStatements.IncomeStatement.NetIncome.TwelveMonths)
        x.DividendsPerShare = x.EarningReports.BasicEPS.TwelveMonths * payout_ratio
        x.DividendYield = x.DividendsPerShare / x.Price

        # sorting by dividend yield
        sorted_by_yield = sorted(fine, key=lambda
            x: x.DividendsPerShare / x.Price if x.Price > 0 and x.DividendsPerShare is not None else 0, reverse=True)
        # calculate halfway point of the sorted list
        half = len(sorted_by_yield) // 2

        # select the upper half
        self.active_universe = [x.Symbol for x in sorted_by_yield[:half]]

        return self.active_universe



    def Rebalance(self):
        # close opened positions
        stocks_invested = [x.Key for x in self.Portfolio if x.Value.Invested]
        for symbol in stocks_invested:
            q_invested: int = self.Portfolio[symbol].Quantity
            self.MarketOnCloseOrder(symbol, -q_invested)

        day_to_check = (self.Time.date() + BDay(1)).date()

        # there are stocks with payday next business day
        if day_to_check in self.dividend_data:
            payday_tickers = list(self.dividend_data[day_to_check].keys())

            long = []
            for symbol in self.active_universe:
                if symbol.Value in payday_tickers:
                    long.append(symbol)

            if len(long) != 0:
                portfolio_value = self.Portfolio.MarginRemaining / len(long)
                for symbol in long:
                    price = self.Securities[symbol].Price
                    if price != 0:
                        q = portfolio_value / price
                        self.MarketOnCloseOrder(symbol, q)

    def Selection(self):
        if self.Time.month % 3 == 0:
            self.selection_flag = True


# custom fee model
class CustomFeeModel(FeeModel):
    def GetOrderFee(self, parameters):
        fee = parameters.Security.Price * parameters.Order.AbsoluteQuantity * 0.00005
        return OrderFee(CashAmount(fee, "USD"))


class DividendInfo():
    def __init__(
            self,
            ticker: str,
            ex_div_date: datetime,
            payday: datetime,
            record_date: datetime,
            dividend_value: float,
            ann_dividend_value: float,
            announcement_date: datetime
    ):
        self.ticker: str = ticker
        self.ex_div_date: datetime = ex_div_date
        self.payday: datetime = payday
        self.record_date: datetime = record_date
        self.dividend_value: float = dividend_value
        self.ann_dividend_value: float = ann_dividend_value
        self.announcement_date: datetime = announcement_date