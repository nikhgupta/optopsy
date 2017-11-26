import collections
from optopsy.backtester.account import Account
from optopsy.backtester.iterator import OptionChainIterator
from optopsy.core.options.option_strategies import OptionStrategies
from optopsy.globals import OrderStatus, OrderType, OrderAction
from optopsy.backtester.event import FillEvent, RejectedEvent
from optopsy.core.options.option_margin import DefaultOptionMargin


class BaseBroker(object):
    def __init__(self, queue, datafeed, margin_rules=None):

        self.datafeed = datafeed
        self.account = Account()
        self.margin_rules = DefaultOptionMargin if margin_rules is None else margin_rules

        # raw options chain data dict
        self.data = {}
        self.dates = list()
        self.data_stream = None
        self.order_list = collections.OrderedDict()

        # events queue to send order events to
        self.queue = queue

        self.continue_backtest = True
        self.current_date = None

    def set_balance(self, balance):
        self.account.set_balance(balance)

    def source(self, symbol, strategy, start=None, end=None, **params):
        """
        Get the option chain data from data source and store in data dict
        :param symbol: symbol to construct datafeed for
        :param strategy: the name of the option strategy to construct, must be same
                         as corresponding function name.
        :param start: start date to get options data for
        :param end: end date to get options data for
        :param params: parameters to build option strategy with
        :return:
        """

        if symbol not in self.data:
            try:
                # we don't have raw option prices for this symbol yet, get it from data source
                option_chains = self.datafeed.get(symbol, start, end)
                # construct the specified option strategy with the option chain data
                opt_strategy = getattr(OptionStrategies, strategy.value)(option_chains, **params)
                # merge all quote dates from this option chain to the current list of quote dates
                new_quote_dates = opt_strategy.get_quote_dates()
                self.dates = sorted(list(set(self.dates) | set(new_quote_dates)))
                # append this new option strategy to the data dictionary
                self.data[symbol] = opt_strategy
                # now that we added a new symbol, create a new iterator and use it to stream data
                self.data_stream = OptionChainIterator(self.dates, self.data)
            except IOError:
                raise

    def stream_next(self):
        """
        Return the next quote date's data event from all subscribed symbol
        :return: A bar event object containing the bar data for all subscribed symbols
        """
        try:
            data_event = next(self.data_stream)
        except StopIteration:
            self.continue_backtest = False
            return

        # update the current state for the broker and it's orders
        self.current_date = data_event.date
        self.update_data(data_event.quotes)

        # Send event to queue
        self.queue.put(data_event)

    def _execute(self, order):
        """
        Execute the order, set status and create fill event.
        :param order:
        :return:
        """
        order.status = OrderStatus.FILLED
        order.executed_price = order.mark

        # update account positions
        self.account.process_order(order)

        event = FillEvent(self.current_date, order)
        self.queue.put(event)

    def _executable(self, order):
        """
        Test execution of an order based on available cash and buying power.
        Check that we have enough option buying power/cash to carry out the order
        :param order: The order to test the executable conditions for
        :return: Boolean
        """
        return ((self.account.cash_balance - order.total_cost > 0) and
                (self.account.option_buying_power - order.margin) > 0)

    def process_order(self, event):
        """
        Process a new order received from an order event.
        """

        if self._executable(event.order):
            # reduce buying power as the order is accepted
            self.account.option_buying_power -= event.order.margin
            self.execute_order(event.order)

            # add the order to the order list to keep track
            self.order_list[event.order.ticket] = event.order
        else:
            event.order.status = OrderStatus.REJECTED
            evt = RejectedEvent(self.current_date, event.order)
            self.queue.put(evt)

    def execute_order(self, order):
        """
        Execute the order event without any additional logic as
        this is a basic implementation.
        :param order: The order created by strategy to execute
        :return: None
        """
        # set the order status, as it is accepted
        order.status = OrderStatus.WORKING

        if order.order_type == OrderType.MKT:
            # this is a market order, execute it immediately at current mark price
            self._execute(order)
        elif order.order_type == OrderType.LMT:
            # this is a limit order, check the limits and execute if able
            # if ((order.action == OrderAction.BUY and order.limit_price >= order.mark) or
            #         (order.action == OrderAction.SELL and order.limit_price <= order.mark)):
            if ((order.action in [OrderAction.BTO, OrderAction.BTC] and order.price >= order.mark) or
               (order.action in [OrderAction.STO, OrderAction.STC] and order.price <= order.mark)):
                # if market conditions meet limit requirements execute it
                self._execute(order)

    def update_data(self, quotes):
        """
        Using fresh quotes from data source, update current values
        for pending orders and positions held in accounts.
        :param quotes: fresh quotes in DataFrame format
        """
        # self.quotes = quotes.fetch()

        # update the broker's working orders' option prices
        # for order_item in self.order_list:
        #     order = self.order_list[order_item]
        #     if order.status == OrderStatus.WORKING:
        #         order.update(self.quotes)
        #         self.execute_order(order)

        # update the account's position values
        # self.account.update(self.quotes)
        pass